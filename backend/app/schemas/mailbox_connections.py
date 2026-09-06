from pydantic import BaseModel


class MailboxConnectionSetRequest(BaseModel):
    mailbox_address: str
