from typing import Optional

from pydantic import BaseModel


class PtyReadRequest(BaseModel):
    session_id: str
    pty_id: str
    cursor: Optional[int] = None
    max_output: Optional[int] = None


class PtySendRequest(BaseModel):
    session_id: str
    pty_id: str
    input: str


class PtyCloseRequest(BaseModel):
    session_id: str
    pty_id: str


class PtyStreamRequest(BaseModel):
    session_id: str
    stream_id: Optional[str] = None
    last_seq: Optional[int] = None
    resume: Optional[bool] = False
