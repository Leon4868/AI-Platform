from fastapi import APIRouter


# Phase one records audit events internally. No audit HTTP operation is part of
# packages/contracts/openapi.yaml yet, so the v1 router must expose none.
router = APIRouter(tags=["audit"])
