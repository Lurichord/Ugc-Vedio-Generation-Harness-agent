import uvicorn

if __name__ == "__main__":
    uvicorn.run("ugc_agent.api.main:create_app", factory=True)
