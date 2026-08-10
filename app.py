--- a/app.py
+++ b/app.py
@@ -0,0 +1,27 @@
+from fastapi import FastAPI, HTTPException

+from fastapi.middleware.cors import CORSMiddleware

+

+app = FastAPI(title="saapp")

+

+app.add_middleware(

+    CORSMiddleware,

+    allow_origins=["*"],

+    allow_credentials=True,

+    allow_methods=["*"],

+    allow_headers=["*"],

+)

+

+@app.get("/")

+async def root():

+    return {"status": "healthy"}

+

+@app.get("/api/erragent-debug")

+async def trigger_error():

+    try:

+        result = 1 / 0

+        return {"result": result}

+    except ZeroDivisionError:

+        raise HTTPException(

+            status_code=400,

+            detail="Division by zero error triggered and handled successfully."

+        )
