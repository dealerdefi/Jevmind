router = Router("/auth")
@router.post("/refresh")
def refresh(body): return rotate_refresh(body["token"])
