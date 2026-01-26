from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

# 允许跨域，因为前端是 localhost:1420 (Tauri)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 在生产环境中应该限制为 Tauri 的地址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

@app.get("/")
def read_root():
    return {"status": "FastAPI is running!"}

@app.post("/chat")
def chat(request: ChatRequest):
    # 模拟 AI 回复
    return {"reply": f"AI 收到你的消息了: {request.message} (来自 Python 后端)"}

if __name__ == "__main__":
    # 启动服务，端口 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)
