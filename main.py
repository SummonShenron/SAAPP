--- a/app.py
+++ b/app.py
@@ -1132,3 +1132,3 @@
 def trigger_error():
-    return 1 / 0
+    return {"status": "healthy", "message": "ErrAgent debug endpoint is operational"}