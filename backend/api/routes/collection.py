from fastapi import APIRouter, UploadFile

router = APIRouter()


@router.post("/import")
async def import_collection(file: UploadFile) -> dict[str, int]:
    # TODO: parse CSV, upsert Collection rows against database/models.py
    raise NotImplementedError
