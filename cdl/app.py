"""FastAPI entry point for CDL HTTP routes.

The application owns request parsing, authentication, and background dispatch.
It must not contain grounding, drafting, or integration business logic.
"""

from fastapi import FastAPI


app = FastAPI(title="Commits Don't Lie")
