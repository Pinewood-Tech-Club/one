"""
Test script to verify Convex integration
"""
from convex_client import update_courses_cache, get_convex_client
from convex import ConvexClient

# Test data
test_user_id = "1"
test_courses = [
    {
        "id": "12345",
        "course_title": "Test Course 1",
        "section_title": "Section A",
        "subject_area": "Math",
    },
    {
        "id": "67890",
        "course_title": "Test Course 2",
        "section_title": "Section B",
        "subject_area": "Science",
    },
]

print(f"Testing Convex integration...")
print(f"User ID: {test_user_id}")
print(f"Courses: {len(test_courses)}")

# Update cache
print("\n1. Updating courses cache...")
result = update_courses_cache(test_user_id, test_courses)
print(f"Result: {result}")

# Query cache
print("\n2. Querying courses cache...")
client = get_convex_client()
courses = client.query("schoologyCache:getCourses", {"userId": test_user_id})
print(f"Retrieved {len(courses)} courses:")
for course in courses:
    print(f"  - {course.get('course_title')} ({course.get('id')})")

print("\nDone!")

