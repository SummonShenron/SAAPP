--- a/app.py
+++ b/app.py
@@ -1133,1 +1133,4 @@
-    return 1 / 0
+    from fastapi import HTTPException
+    raise HTTPException(
+        status_code=500, detail="This is a controlled error trigger for testing."
+    )