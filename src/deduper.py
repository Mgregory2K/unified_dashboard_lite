from storage import item_exists


def is_duplicate(url: str) -> bool:
    return item_exists(url)
