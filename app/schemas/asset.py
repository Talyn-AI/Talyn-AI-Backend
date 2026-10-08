"""Upload + asset schemas."""
from pydantic import BaseModel, Field


class PresignIn(BaseModel):
    purpose: str = Field(..., min_length=1, max_length=30)
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(..., min_length=1, max_length=120)
    # Declared size, checked against the purpose's cap before a key is
    # minted. Unverifiable as a fact — verify() reads the real size from the
    # provider — but rejecting an obvious overshoot here saves the uploader
    # waiting through an upload that would be refused anyway.
    size_bytes: int = Field(default=0, ge=0)


class PresignOut(BaseModel):
    """What the client needs to PUT a file to storage directly.

    ``max_bytes`` is advisory — a PUT URL cannot enforce it, so the client
    should reject an oversized file instantly instead of uploading bytes
    that verify() will delete anyway. Send exactly the declared
    Content-Type header: it is part of the signature, and anything else
    fails with SignatureDoesNotMatch.
    """

    upload_url: str
    storage_key: str
    expires_in: int
    max_bytes: int
    method: str = "PUT"


class AssetIn(BaseModel):
    kind: str = Field(..., min_length=1, max_length=20)
    storage_key: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    filename: str = Field(default="", max_length=255)


class AssetRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    kind: str
    storage_key: str | None = None
    url: str | None = None
    filename: str = ""
    # The provider's number, not the client's.
    size_bytes: int = 0
    scan_status: str = "unscanned"
    scan_detail: str = ""


class MaterialPresignIn(BaseModel):
    """Ask for an upload form for one library document.

    No purpose field: the purpose is always "material" on this route, so there
    is nothing to choose and nothing to get wrong.
    """

    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(..., min_length=1, max_length=120)
    # Declared size, checked against the library quota before a key is
    # minted. Unverifiable as a fact — claim() reads the real size from the
    # provider — but rejecting an obvious overshoot here saves the uploader
    # waiting through an upload that would be refused anyway.
    size_bytes: int = Field(default=0, ge=0)


class MaterialClaimIn(BaseModel):
    """Claim a finished upload into the library."""

    storage_key: str = Field(..., min_length=1, max_length=500)


class MaterialRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    filename: str = ""
    storage_key: str = ""
    content_type: str = ""
    # The provider's number, not the client's.
    size_bytes: int = 0
    scan_status: str = "unscanned"
    scan_detail: str = ""
