from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router

app = FastAPI(title="ParcelPilot Support Agent")

# Wide open for a hosted demo talking to a separate React frontend.
# Tighten allow_origins to your actual frontend URL before submission if
# you want to be stricter.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.include_router(router)
@app.get("/health") 
def health(): 
    return {"status": "healthy"}
