import pathlib

def get_debugtools_dir() -> pathlib.Path | None:
    # return the directory containing this file
    try:
        return pathlib.Path(__file__).parent.resolve()
    except Exception:
        return None