"""FastAPI dependency providers - resolve collaborators from the container."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from ifap.api.container import Container


def get_container(request: Request) -> Container:
    return cast("Container", request.app.state.container)


ContainerDep = Annotated[Container, Depends(get_container)]
