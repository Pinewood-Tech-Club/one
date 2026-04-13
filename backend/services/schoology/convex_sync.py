"""
Convex cache synchronization functions.
"""
from services.convex_bridge import call_bridge_action


def sync_courses(convex_url: str, user_id: str, courses: list[dict]):
    """
    Update courses cache in Convex.
    """
    _ = convex_url
    return call_bridge_action("updateCourses", {"userId": user_id, "courses": courses})


def sync_assignments(convex_url: str, course_id: str, assignments: list[dict]):
    """
    Update assignments cache in Convex.
    """
    _ = convex_url
    return call_bridge_action(
        "updateAssignments",
        {"courseId": course_id, "assignments": assignments},
    )


def sync_assignment_user_state(
    convex_url: str,
    user_id: str,
    course_id: str,
    assignments: list[dict],
):
    """
    Update per-user assignment state cache in Convex.
    """
    _ = convex_url
    return call_bridge_action(
        "updateAssignmentUserState",
        {"userId": user_id, "courseId": course_id, "assignments": assignments},
    )


def sync_course_assignments(
    convex_url: str,
    user_id: str,
    course_id: str,
    assignments: list[dict],
):
    """
    Update shared assignments and per-user assignment state for a course.
    """
    _ = convex_url
    return call_bridge_action(
        "updateCourseAssignments",
        {"userId": user_id, "courseId": course_id, "assignments": assignments},
    )


def sync_profile_picture(convex_url: str, user_id: str, picture_url: str):
    """
    Update user's profile picture URL in Convex.
    """
    _ = convex_url
    return call_bridge_action(
        "updateProfilePicture",
        {"userId": user_id, "pictureUrl": picture_url},
    )


def clear_cache(convex_url: str, user_id: str):
    """
    Clear all cached Schoology data for a user.
    """
    _ = convex_url
    return call_bridge_action("clearSchoologyCache", {"userId": user_id})
