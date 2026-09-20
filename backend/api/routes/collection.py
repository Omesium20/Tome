from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.orm import Session

from api.schemas import CollectionImportResponse
from database.local.session import get_session

router = APIRouter()


@router.post("/import", response_model=CollectionImportResponse)
async def import_collection(
    file: UploadFile,
    session: Session = Depends(get_session),
) -> CollectionImportResponse:
    # TODO: parse CSV, resolve card names to oracle ids via the Knowledge API,
    # then upsert Collection rows from database/local/models.py
    raise NotImplementedError
