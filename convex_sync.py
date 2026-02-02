"""
Convex cache synchronization functions
"""
from convex import ConvexClient


def _get_client(convex_url: str) -> ConvexClient:
    """Get a Convex client instance"""
    return ConvexClient(convex_url)


def sync_courses(convex_url: str, user_id: str, courses: list[dict]):
    """
    Update courses cache in Convex

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
        courses: List of course dictionaries from Schoology API (full section.__dict__)
    """
    client = _get_client(convex_url)

    result = client.mutation("schoologyCache:updateCourses", {
        "userId": user_id,
        "courses": courses,
    })

    return result


def sync_assignments(convex_url: str, user_id: str, course_id: str, assignments: list[dict]):
    """
    Update assignments cache in Convex

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
        course_id: Course/section ID (string)
        assignments: List of assignment dictionaries from Schoology API (full objects)
    """
    client = _get_client(convex_url)

    result = client.mutation("schoologyCache:updateAssignments", {
        "userId": user_id,
        "courseId": course_id,
        "assignments": assignments,
    })

    return result


def sync_upcoming(convex_url: str, user_id: str, assignments: list[dict]):
    """
    Update upcoming assignments cache in Convex

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
        assignments: List of upcoming assignment dictionaries with course info
    """
    client = _get_client(convex_url)

    result = client.mutation("schoologyCache:updateUpcoming", {
        "userId": user_id,
        "assignments": assignments,
    })

    return result


def sync_profile_picture(convex_url: str, user_id: str, picture_url: str):
    """
    Update user's profile picture URL in Convex

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
        picture_url: Schoology profile picture URL
    """
    client = _get_client(convex_url)

    result = client.mutation("users:updateProfilePicture", {
        "userId": user_id,
        "pictureUrl": picture_url,
    })

    return result


def clear_cache(convex_url: str, user_id: str):
    """
    Clear all cached Schoology data for a user

    Args:
        convex_url: Convex deployment URL
        user_id: User ID (string)
    """
    client = _get_client(convex_url)

    result = client.mutation("schoologyCache:clearCache", {
        "userId": user_id,
    })

    return result
