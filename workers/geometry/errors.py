class GeometryError(Exception):
    """An actionable failure; never replace it with synthetic geometry."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self)}
