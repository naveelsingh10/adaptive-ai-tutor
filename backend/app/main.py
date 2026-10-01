from fastapi import FastAPI

app = FastAPI(title="Adaptive AI Tutor API")


@app.get("/health")
def health_check():
    return {"status": "ok"}
