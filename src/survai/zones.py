Point = tuple[float, float]


def inside(point: Point, polygon: list[Point], *, include_boundary: bool = False) -> bool:
    x, y = point
    result = False
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        if (
            include_boundary
            and min(ax, bx) <= x <= max(ax, bx)
            and min(ay, by) <= y <= max(ay, by)
            and abs(side((ax, ay), (bx, by), point)) < 1e-9
        ):
            return True
        if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
            result = not result
    return result


def side(a: Point, b: Point, p: Point) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def crosses(start: Point, end: Point, line: tuple[Point, Point]) -> bool:
    a, b = line
    return (
        side(a, b, start) * side(a, b, end) < 0
        and side(start, end, a) * side(start, end, b) <= 0
    )
