"""
Convex client for updating cache from backend
"""
from convex import ConvexClient
from config import Config


def get_convex_client():
    """Get a Convex client instance"""
    return ConvexClient(Config.CONVEX_URL)


def update_courses_cache(user_id: str, courses: list):
    """
    Update courses cache in Convex

    Args:
        user_id: User ID (string)
        courses: List of course dictionaries from Schoology API (full section.__dict__)
    """
    client = get_convex_client()

    # Pass through the full course objects - Convex will store them as-is
    # Call Convex mutation
    result = client.mutation("schoologyCache:updateCourses", {
        "userId": user_id,
        "courses": courses,
    })

    return result


def update_assignments_cache(user_id: str, course_id: str, assignments: list):
    """
    Update assignments cache in Convex

    Args:
        user_id: User ID (string)
        course_id: Course ID (string)
        assignments: List of assignment dictionaries from Schoology API (full objects)
    """
    client = get_convex_client()

    # Pass through the full assignment objects - Convex will store them as-is
    # Call Convex mutation
    result = client.mutation("schoologyCache:updateAssignments", {
        "userId": user_id,
        "courseId": course_id,
        "assignments": assignments,
    })

    return result


def clear_cache(user_id: str):
    """
    Clear all cached Schoology data for a user
    
    Args:
        user_id: User ID (string)
    """
    client = get_convex_client()
    
    result = client.mutation("schoologyCache:clearCache", {
        "userId": user_id,
    })
    
    return result

