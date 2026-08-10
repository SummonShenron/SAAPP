--- a/app.py
+++ b/app.py
@@ -725,3 +725,4 @@
-def trigger_error(request):
-    return 1 / 0
+from fastapi import HTTPException
+def trigger_error(request):
+    raise HTTPException(status_code=400, detail="Simulated error for debugging purposes")