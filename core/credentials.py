"""Encrypted credential manager using Fernet symmetric encryption."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from core.database import Database
from core.exceptions import CredentialError

logger = logging.getLogger(__name__)


class CredentialManager:
    """Manages storage and retrieval of encrypted network device credentials.

    Sensitive fields (password, snmp_community, enable_secret) are encrypted
    at rest using Fernet (AES-128-CBC).  The encryption key can be supplied
    directly or read from an environment variable.  If no key is available a
    new one is generated and a warning is emitted so the operator can persist
    it.
    """

    def __init__(self, db: Database, encryption_key: str | None = None) -> None:
        self._db = db

        if encryption_key:
            try:
                self._fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)
            except Exception as exc:
                raise CredentialError(f"Invalid encryption key: {exc}") from exc
        else:
            generated_key = Fernet.generate_key()
            self._fernet = Fernet(generated_key)
            logger.warning(
                "No encryption key provided -- a new key was generated. "
                "Credentials will NOT persist across restarts. "
                "Generate a permanent key with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
                "and set it as the NETAGENT_ENCRYPTION_KEY environment variable."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def store(
        self,
        name: str,
        username: str,
        password: str | None = None,
        ssh_key_path: str | None = None,
        snmp_community: str | None = None,
        enable_secret: str | None = None,
    ) -> str:
        """Encrypt sensitive fields and store the credential. Returns the new credential id."""
        credential_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        encrypted_password = self._encrypt(password) if password else None
        encrypted_snmp = self._encrypt(snmp_community) if snmp_community else None
        encrypted_enable = self._encrypt(enable_secret) if enable_secret else None

        try:
            await self._db.add_credential(
                id=credential_id,
                name=name,
                username=username,
                password=encrypted_password,
                ssh_key_path=ssh_key_path,
                snmp_community=encrypted_snmp,
                enable_secret=encrypted_enable,
                created_at=now,
            )
            logger.info("Stored credential %r (id=%s)", name, credential_id)
            return credential_id
        except Exception as exc:
            raise CredentialError(f"Failed to store credential {name!r}: {exc}") from exc

    async def retrieve(self, credential_id: str) -> dict[str, Any]:
        """Retrieve and decrypt a credential by id.

        Returns a dict with plaintext values for password, snmp_community, and
        enable_secret.

        Raises
        ------
        CredentialError
            If the credential is not found or decryption fails.
        """
        try:
            row = await self._db.get_credential(credential_id)
        except Exception as exc:
            raise CredentialError(f"Failed to retrieve credential {credential_id}: {exc}") from exc

        if row is None:
            raise CredentialError(f"Credential {credential_id} not found")

        try:
            result: dict[str, Any] = {
                "id": row["id"],
                "name": row["name"],
                "username": row["username"],
                "password": self._decrypt(row["password"]) if row.get("password") else None,
                "ssh_key_path": row.get("ssh_key_path"),
                "snmp_community": self._decrypt(row["snmp_community"]) if row.get("snmp_community") else None,
                "enable_secret": self._decrypt(row["enable_secret"]) if row.get("enable_secret") else None,
                "created_at": row.get("created_at"),
            }
            return result
        except InvalidToken as exc:
            raise CredentialError(
                f"Decryption failed for credential {credential_id}. "
                "The encryption key may have changed."
            ) from exc

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all credentials with sensitive fields masked.

        Password, snmp_community, and enable_secret are replaced with ``"****"``
        if they have a stored value, or ``None`` if they were never set.
        """
        try:
            rows = await self._db.list_credentials()
        except Exception as exc:
            raise CredentialError(f"Failed to list credentials: {exc}") from exc

        masked: list[dict[str, Any]] = []
        for row in rows:
            masked.append({
                "id": row["id"],
                "name": row["name"],
                "username": row["username"],
                "password": "****" if row.get("password") else None,
                "ssh_key_path": row.get("ssh_key_path"),
                "snmp_community": "****" if row.get("snmp_community") else None,
                "enable_secret": "****" if row.get("enable_secret") else None,
                "created_at": row.get("created_at"),
            })
        return masked

    async def delete(self, credential_id: str) -> None:
        """Delete a credential by id.

        Raises
        ------
        CredentialError
            If the credential does not exist or the delete fails.
        """
        try:
            deleted = await self._db.delete_credential(credential_id)
        except Exception as exc:
            raise CredentialError(f"Failed to delete credential {credential_id}: {exc}") from exc

        if not deleted:
            raise CredentialError(f"Credential {credential_id} not found")

        logger.info("Deleted credential %s", credential_id)

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string and return the base64-encoded ciphertext."""
        if not plaintext:
            return ""
        token: bytes = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt a base64-encoded Fernet token back to plaintext."""
        if not ciphertext:
            return ""
        plaintext: bytes = self._fernet.decrypt(ciphertext.encode("utf-8"))
        return plaintext.decode("utf-8")
