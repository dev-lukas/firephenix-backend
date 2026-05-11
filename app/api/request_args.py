RANKING_SEARCH_MAX_LENGTH = 255


def positive_int_arg(args, name, default, *, max_value=None):
    raw_value = args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value < 1:
        raise ValueError(f"{name} must be at least 1")

    if max_value is not None:
        value = min(value, max_value)

    return value


def search_arg(args, name="search", *, max_length=RANKING_SEARCH_MAX_LENGTH):
    value = (args.get(name, "") or "").strip()
    if len(value) > max_length:
        raise ValueError(f"{name} must be at most {max_length} characters")
    return value


def ranking_request_args(args, *, default_limit, max_limit):
    return (
        positive_int_arg(args, "page", 1),
        positive_int_arg(args, "limit", default_limit, max_value=max_limit),
        search_arg(args),
    )


def pages_for(total_count, limit):
    return (total_count + limit - 1) // limit


def clamp_page_to_total(page, total_count, limit):
    return min(page, max(1, pages_for(total_count, limit)))
