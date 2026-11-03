"""Shared fakes for the credential-store tests (HDW-401): an in-memory keyring, never the OS one.

The suite runs on Windows, where the real backend is the Windows Credential Manager: a test that
reached it could read a developer's key or leave one behind. Every store in the suite is built
with ``backend=FakeKeyring()``.
"""

from __future__ import annotations

from keyring.errors import PasswordDeleteError

SECRET = "sk-or-v1-9f8e7d6c5b4a39281706f5e4d3c2b1a0"
OTHER_SECRET = "sk-ant-api03-Zx8Cv6Bn4Mm2Ll0Kk9Jj7Hh5Gg3Ff1Dd"
ENV_SECRET = "sk-or-v1-env0123456789abcdef"


class FakeKeyring:
    """The three methods this code calls on ``keyring``, held in a dict."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        if (service_name, username) not in self.values:
            raise PasswordDeleteError("no such password")
        del self.values[(service_name, username)]

    def payload(self) -> str:
        return "\n".join(self.values.values())


class BrokenKeyring(FakeKeyring):
    """A locked or broken store: every read and write fails."""

    def get_password(self, service_name: str, username: str) -> str | None:
        raise OSError("the credential store is locked")

    def set_password(self, service_name: str, username: str, password: str) -> None:
        raise OSError("the credential store is locked")

    def delete_password(self, service_name: str, username: str) -> None:
        raise OSError("the credential store is locked")
