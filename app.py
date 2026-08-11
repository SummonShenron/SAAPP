diff --git a/app.py b/app.py
index 2f3dd11..fe4396f 100644
--- a/app.py
+++ b/app.py
@@ -1130,7 +1130,13 @@ async def ingest_error_webhook(
 async def trigger_error():
     logger.info("--> /api/erragent-debug endpoint hit!")
     # Intentionally trigger zero division; caught automatically by global_exception_handler!
-    return 1 / 0
+    try:
+        return 1 / 0
+    except ZeroDivisionError:
+        raise HTTPException(
+            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
+            detail="ZeroDivisionError: division by zero"
+        )
     
 @app.post("/webhooks/github")
 async def github_webhook(request: Request, background_tasks: BackgroundTasks):
