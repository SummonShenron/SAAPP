"""The other end of a proposed edit: does it break a file it does not touch? (2026-11-24 coding audit.)

WHY. A proposal is checked file by file (backend/services/local_edits.py: the old text matches, the result parses, every name it uses is defined or imported). Nothing looked ACROSS files, and that is
where multi-file changes went wrong: 35% of proposals in a month were rework of a file applied minutes before. The cases code can decide exactly:

1. Python: a top-level function, class or name the proposal removes or renames that another file still imports (`from pkg.mod import name`) or reaches as `mod.name`.
2. Python: a function whose new signature its callers no longer fit (a call passes fewer positional arguments than it now requires, or a keyword it no longer accepts), in any file that imports it,
   and in the file itself.
3. Python: a new `from pkg.mod import name` (in an edited file) of a name the folder's module does not define after the proposal.
4. JS/TS: a named export the proposal removes that another file still imports from it by a relative path, and a new named import (in an edited file) of something the target file does not export.

Anything uncertain is left alone (a star import, `__getattr__`, `__all__` tricks, an import from a package outside the folder, a default export, a call with *args or **kwargs). Pure: the caller hands
in the edited files' before/after text and a way to read the rest of the folder.
"""
import ast
import posixpath
import re
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

MAX_PROBLEMS = 8
MAX_SCANNED_FILES = 3000
MAX_SCANNED_BYTES = 30_000_000
PY = (".py",)
JS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_RESOLVE = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")


# ---------------------------------------------------------------------------------------------- Python

def module_name(path: str) -> Optional[str]:
    """"backend/utils/x.py" -> "backend.utils.x"; a package's __init__.py -> the package. Pure."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


def _parse(text: Optional[str]) -> Optional[ast.Module]:
    try:
        return ast.parse(text or "")
    except SyntaxError:
        return None


class _Signature:
    def __init__(self, node: ast.AST):
        args = node.args
        positional = list(args.posonlyargs) + list(args.args)
        if positional and positional[0].arg in ("self", "cls"):
            positional = positional[1:]
        self.max_positional = None if args.vararg else len(positional)
        self.required_positional = len(positional) - len(args.defaults)
        self.required_names = [a.arg for a in positional[: self.required_positional]]       # in order: a call fills them by position first, then by name
        self.keywords = {a.arg for a in list(args.args) + list(args.kwonlyargs)}
        self.required_keyword_only = {a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is None}
        self.any_keyword = args.kwarg is not None
        self.is_class = False


def top_level(text: Optional[str]) -> Optional[Dict[str, object]]:
    """{name: _Signature | True} for what a Python module defines at top level, or None when it cannot be judged (does not parse, star import, module __getattr__). Pure."""
    tree = _parse(text)
    if tree is None:
        return None
    names: Dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "__getattr__":
                return None
            names[node.name] = _Signature(node)
        elif isinstance(node, ast.ClassDef):
            names[node.name] = True
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names[sub.id] = True
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names[(alias.asname or alias.name).split(".")[0]] = True
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            for sub in ast.walk(node):                     # names defined under a guard (try/except ImportError, if TYPE_CHECKING) still exist
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.setdefault(sub.name, True)
                elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    names.setdefault(sub.id, True)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.setdefault((alias.asname or alias.name).split(".")[0], True)
    return names


def _resolve_from(importer: str, node: ast.ImportFrom) -> Optional[str]:
    if not node.level:
        return node.module
    package = importer[:-3].split("/")[:-1] if importer.endswith(".py") else []
    if node.level > 1:
        package = package[: len(package) - (node.level - 1)]
    return ".".join(package + ([node.module] if node.module else [])) or None


def _python_imports(path: str, tree: ast.Module) -> Tuple[List[Tuple[str, str, int, str]], Dict[str, str]]:
    """([(module, name, line, local_name)] from `from x import y`), {local alias: module} for `import x.y as z`. Pure."""
    froms, modules = [], {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = _resolve_from(path, node)
            if source:
                for alias in node.names:
                    if alias.name != "*":
                        froms.append((source, alias.name, node.lineno, alias.asname or alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name] = alias.name
    return froms, modules


def _call_misfit(call: ast.Call, signature: "_Signature") -> Optional[str]:
    if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        return None                                        # *args / **kwargs at the call: cannot tell
    given = len(call.args)
    named = {k.arg for k in call.keywords}
    if signature.max_positional is not None and given > signature.max_positional:
        return f"passes {given} positional arguments, the new signature takes at most {signature.max_positional}"
    missing = [name for name in signature.required_names[given:] if name not in named]
    if missing:
        return f"passes {given} positional argument{'s' if given != 1 else ''}, the new signature requires {signature.required_positional}"
    unknown = sorted(named - signature.keywords) if not signature.any_keyword else []
    if unknown:
        return f"passes {', '.join(unknown)}=, which the new signature no longer accepts"
    absent = sorted(signature.required_keyword_only - named)
    if absent:
        return f"does not pass {', '.join(absent)}=, which the new signature requires"
    return None


# ---------------------------------------------------------------------------------------------- JS / TS

_EXPORT_DECL_RE = re.compile(r"^\s*export\s+(?:declare\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum|abstract\s+class)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
_EXPORT_LIST_RE = re.compile(r"^\s*export\s+(?:type\s+)?\{([^}]*)\}", re.MULTILINE)
_EXPORT_STAR_RE = re.compile(r"^\s*export\s+\*", re.MULTILINE)
_NAMED_IMPORT_RE = re.compile(r"^\s*import\s+(?:type\s+)?(?:[A-Za-z_$][\w$]*\s*,\s*)?\{([^}]*)\}\s*from\s*['\"](\.{1,2}/[^'\"]+)['\"]", re.MULTILINE)


def js_exports(text: Optional[str]) -> Optional[Set[str]]:
    """The named exports of a JS/TS file, or None when they cannot be judged (an `export *`). Pure."""
    if text is None:
        return set()
    if _EXPORT_STAR_RE.search(text):
        return None
    names = set(_EXPORT_DECL_RE.findall(text))
    for listed in _EXPORT_LIST_RE.findall(text):
        for part in listed.split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip().replace("type ", "").strip())
    return names


def _named_imports(text: str) -> List[Tuple[str, str, int]]:
    """[(imported name, relative specifier, line)] for `import { a, b as c } from './x'`. Pure."""
    found = []
    for match in _NAMED_IMPORT_RE.finditer(text or ""):
        line = text.count("\n", 0, match.start()) + 1
        for part in match.group(1).split(","):
            part = part.strip().replace("type ", "", 1).strip()
            if part:
                found.append((part.split(" as ")[0].strip(), match.group(2), line))
    return found


def _resolve_js(importer: str, specifier: str, exists: Callable[[str], bool]) -> Optional[str]:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
    for suffix in _JS_RESOLVE:
        if exists(base + suffix):
            return base + suffix
    return None


# ---------------------------------------------------------------------------------------------- the check

def check(before: Dict[str, Optional[str]], after: Dict[str, Optional[str]], paths: Iterable[str], read: Callable[[str], Optional[str]]) -> List[str]:
    """The problems the proposal would cause in other files (and in its own calls), as sentences with file:line, at most MAX_PROBLEMS. `before`/`after`: every file the proposal touches, None for a
    file that does not exist (before) or is gone (after). `paths`: every file in the folder. `read(path)`: a file's current text. Pure apart from `read`."""
    paths = [p for p in paths if p.endswith(PY + JS)]
    state = lambda path: after[path] if path in after else read(path)   # noqa: E731  the folder as it will be
    exists = lambda path: (after[path] is not None) if path in after else path in path_set  # noqa: E731
    path_set = set(paths)
    problems: List[str] = []

    # what changed, per edited module
    py_removed: Dict[str, Dict[str, str]] = {}          # module -> {name: path}
    py_signature: Dict[str, Dict[str, "_Signature"]] = {}
    py_module_names: Dict[str, Optional[Dict[str, object]]] = {}
    js_removed: Dict[str, Set[str]] = {}
    for path in after:
        if path.endswith(PY):
            module = module_name(path)
            old, new = top_level(before.get(path)), top_level(after[path]) if after[path] is not None else {}
            if module and old is not None and new is not None:
                gone = {n: path for n in old if n not in new and not n.startswith("__")}
                if gone:
                    py_removed[module] = gone
                changed = {n: s for n, s in new.items() if isinstance(s, _Signature) and isinstance(old.get(n), _Signature) and vars(s) != vars(old[n])}
                if changed:
                    py_signature[module] = changed
            if module:
                py_module_names[module] = new
        elif path.endswith(JS):
            old, new = js_exports(before.get(path)), js_exports(after[path]) if after[path] is not None else set()
            if old is not None and new is not None and old - new:
                js_removed[path] = old - new

    # A file the proposal does not touch can only be broken by it if it mentions a name that changed: anything else is skipped without being parsed.
    watched = {n for names in py_removed.values() for n in names} | {n for names in py_signature.values() for n in names} | {n for names in js_removed.values() for n in names}
    scanned_bytes = 0
    for path in paths[:MAX_SCANNED_FILES]:
        if len(problems) >= MAX_PROBLEMS:
            break
        edited = path in after
        text = state(path)
        if not text or (not edited and not any(name in text for name in watched)):
            continue
        scanned_bytes += len(text)
        if scanned_bytes > MAX_SCANNED_BYTES:
            break
        if path.endswith(PY) and (py_removed or py_signature or edited):
            tree = _parse(text)
            if tree is None:
                continue
            froms, modules = _python_imports(path, tree)
            old_tree = _parse(before.get(path)) if edited else None
            already = {(m, n) for m, n, _l, _a in _python_imports(path, old_tree)[0]} if old_tree is not None else set()
            for module, name, line, _local in froms:
                if name in py_removed.get(module, {}):
                    problems.append(f"{path}:{line} imports {name} from {module}, which this proposal removes from {py_removed[module][name]}. Update that import (and its uses) in the same proposal")
                    continue
                if not edited or (module, name) in already:
                    continue                                   # only an import this proposal adds is checked against its module
                base = module.replace(".", "/")
                module_path = next((p for p in (base + ".py", base + "/__init__.py") if exists(p)), None)
                if module_path is None:
                    continue                                   # a module outside the folder (a library): not ours to judge
                names = py_module_names[module] if module in py_module_names else top_level(state(module_path))
                is_submodule = exists(f"{base}/{name}.py") or exists(f"{base}/{name}/__init__.py")
                if names is not None and name not in names and not is_submodule:
                    problems.append(f"{path}:{line} imports {name} from {module}, but {module} does not define {name}. Import a name that exists, or add it to that module in the same proposal")
            # calls whose arguments no longer fit a changed signature, and mod.name uses of a removed name
            local_sigs: Dict[str, "_Signature"] = {}
            for module, name, _line, local in froms:
                if name in py_signature.get(module, {}):
                    local_sigs[local] = py_signature[module][name]
            own = module_name(path)
            if edited and own in py_signature:
                local_sigs.update(py_signature[own])
            module_aliases = {alias: mod for alias, mod in modules.items() if mod in py_removed or mod in py_signature}
            for module, name, _line, local in froms:                  # `from backend.utils import low_moves` then low_moves.choose(...): the name is itself a module
                if f"{module}.{name}" in py_removed or f"{module}.{name}" in py_signature:
                    module_aliases[local] = f"{module}.{name}"
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in module_aliases:
                    mod = module_aliases[node.value.id]
                    if node.attr in py_removed.get(mod, {}):
                        problems.append(f"{path}:{node.lineno} uses {node.value.id}.{node.attr}, which this proposal removes from {py_removed[mod][node.attr]}. Update it in the same proposal")
                if isinstance(node, ast.Call):
                    target = None
                    if isinstance(node.func, ast.Name) and node.func.id in local_sigs:
                        target, label = local_sigs[node.func.id], node.func.id
                    elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in module_aliases:
                        mod = module_aliases[node.func.value.id]
                        target, label = py_signature.get(mod, {}).get(node.func.attr), f"{node.func.value.id}.{node.func.attr}"
                    if target is not None:
                        misfit = _call_misfit(node, target)
                        if misfit:
                            problems.append(f"{path}:{node.lineno} calls {label}(...) and {misfit}. Update the call in the same proposal")
                if len(problems) >= MAX_PROBLEMS:
                    break
        elif path.endswith(JS):
            already = {(n, s) for n, s, _l in _named_imports(before.get(path) or "")} if edited else set()
            for name, specifier, line in _named_imports(text):
                target = _resolve_js(path, specifier, exists)
                if target is None:
                    continue
                if name in js_removed.get(target, set()):
                    problems.append(f"{path}:{line} imports {name} from '{specifier}', which this proposal removes from {target}. Update that import (and its uses) in the same proposal")
                elif edited and (name, specifier) not in already and target.endswith(JS):
                    exported = js_exports(state(target))
                    if exported is not None and name not in exported:
                        problems.append(f"{path}:{line} imports {name} from '{specifier}', but {target} does not export {name}. Import a name it exports, or export it in the same proposal")
    seen, unique = set(), []
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return unique[:MAX_PROBLEMS]
