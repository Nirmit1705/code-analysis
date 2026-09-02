from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def register():
    pass


@router.post("/login")
def login():
    pass


@router.get("/me")
def me():
    pass
