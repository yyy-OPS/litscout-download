"""Versioned, cloud-independent paper list submission contract."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = r"^[A-Za-z0-9_-]+$"


def public_url(value: str) -> str:
    parts = urlsplit(value)
    host = (parts.hostname or "").lower().rstrip(".")
    if (parts.scheme != "https" or not host or parts.username or parts.password
            or parts.port not in (None, 443) or "." not in host
            or re.search(r"(^|\.)(localhost|local|internal)$", host)
            or re.match(r"^\d+\.", host) or any(ord(c) < 33 for c in value)):
        raise ValueError("Expected a public HTTPS publisher URL on port 443")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return value
    raise ValueError("IP literal URLs are not accepted")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Identifiers(Model):
    doi: str | None = Field(default=None, max_length=300)
    arxivId: str | None = Field(default=None, max_length=100)
    pmid: str | None = Field(default=None, max_length=30)

    @field_validator("doi")
    @classmethod
    def doi_format(cls, value):
        if value is None:
            return value
        value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.I)
        if not re.fullmatch(r"10\.\d{4,9}/[^\s<>\"]+", value):
            raise ValueError("Invalid DOI")
        return value.lower()


class Links(Model):
    landingPage: str | None = Field(default=None, max_length=4096)
    pdfUrl: str | None = Field(default=None, max_length=4096)
    doiUrl: str | None = Field(default=None, max_length=4096)
    arxivUrl: str | None = Field(default=None, max_length=4096)
    sourceUrls: dict[str, str] = Field(default_factory=dict, max_length=10)

    @field_validator("landingPage", "pdfUrl", "doiUrl", "arxivUrl")
    @classmethod
    def validate_url(cls, value):
        return public_url(value) if value else value

    @field_validator("sourceUrls")
    @classmethod
    def validate_sources(cls, values):
        for key, value in values.items():
            if len(key) > 80 or len(value) > 4096:
                raise ValueError("Source URL too long")
            public_url(value)
        return values


class Paper(Model):
    id: str | None = Field(default=None, min_length=1, max_length=100, pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=1000)
    identifiers: Identifiers = Field(default_factory=Identifiers)
    links: Links = Field(default_factory=Links)
    authors: list[str] = Field(default_factory=list, max_length=100)
    year: int | None = Field(default=None, ge=1000, le=2200)
    venue: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value):
        if not value.strip():
            raise ValueError("Title cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def usable_locator(self):
        if self.identifiers.doi and not self.links.doiUrl:
            self.links.doiUrl = "https://doi.org/" + self.identifiers.doi
        if not any((self.links.landingPage, self.links.pdfUrl, self.links.doiUrl,
                    self.links.arxivUrl, self.links.sourceUrls)):
            raise ValueError("Provide a DOI or at least one public HTTPS article/PDF URL")
        return self


class TaskRequest(Model):
    schemaVersion: int = Field(default=1, ge=1, le=1)
    listId: str | None = Field(default=None, min_length=1, max_length=100, pattern=ID_PATTERN)
    cloudJobId: str | None = Field(default=None, min_length=1, max_length=100, pattern=ID_PATTERN)
    browserId: str = Field(min_length=36, max_length=36, pattern=r"^[a-f0-9-]{36}$")
    taskName: str = Field(default="", max_length=120)
    papers: list[Paper] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_papers(self):
        ids, identities = set(), set()
        for index, paper in enumerate(self.papers, 1):
            paper.id = paper.id or f"paper-{index:03d}"
            identity = paper.identifiers.doi or re.sub(r"\W+", "", paper.title.casefold())
            if paper.id in ids or identity in identities:
                raise ValueError("Duplicate paper ID or identity in this list")
            ids.add(paper.id)
            identities.add(identity)
        return self
