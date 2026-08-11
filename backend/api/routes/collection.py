from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.orm import Session

from api.schemas import CollectionImportResponse
from database.session import get_session

router = APIRouter()


@router.post("/import", response_model=CollectionImportResponse)
async def import_collection(
    file: UploadFile,
    session: Session = Depends(get_session),
) -> CollectionImportResponse:
    # TODO: parse CSV, upsert Collection rows against database/models.py
    raise NotImplementedError
