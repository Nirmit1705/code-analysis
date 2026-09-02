from fastapi import APIRouter

router = APIRouter(tags=["items"])


@router.get("/items")
def list_items():
    pass


@router.get("/items/{item_id}")
def get_item(item_id: int):
    pass
