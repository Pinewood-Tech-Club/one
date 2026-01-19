"""
Schoology API client wrapper
"""
import schoolopy
import requests_oauthlib
from datetime import datetime, timedelta
from .convex_sync import sync_courses, sync_assignments, sync_upcoming, clear_cache


class SchoologyService:
    """
    Wrapper around schoolopy that handles Schoology API calls and Convex synchronization.

    Usage:
        service = SchoologyService(
            user_id="123",
            access_token="token",
            access_token_secret="secret",
            consumer_key="key",
            consumer_secret="secret",
            convex_url="https://...",
            schoology_domain="https://app.schoology.com"
        )

        courses = service.get_courses()  # Fetches and syncs to Convex
        assignments = service.get_upcoming_assignments(days=7)
    """

    def __init__(self, user_id: str, access_token: str, access_token_secret: str,
                 consumer_key: str, consumer_secret: str, convex_url: str,
                 schoology_domain: str = "https://app.schoology.com"):
        """
        Initialize Schoology service

        Args:
            user_id: User ID string
            access_token: OAuth access token
            access_token_secret: OAuth access token secret
            consumer_key: Schoology consumer key
            consumer_secret: Schoology consumer secret
            convex_url: Convex deployment URL
            schoology_domain: Schoology domain URL (default: https://app.schoology.com)
        """
        self.user_id = user_id
        self.convex_url = convex_url
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret

        # Create schoolopy auth object
        auth = schoolopy.Auth(
            consumer_key,
            consumer_secret,
            three_legged=True,
            domain=schoology_domain,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )

        # Recreate the OAuth session with access tokens
        # This is required for API calls to work properly
        auth.oauth = requests_oauthlib.OAuth1Session(
            consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_token_secret,
        )

        self.sc = schoolopy.Schoology(auth)

    def get_courses(self, sync_to_convex: bool = True) -> list[dict]:
        """
        Fetch user's courses from Schoology

        Args:
            sync_to_convex: Whether to sync to Convex cache (default: True)

        Returns:
            List of course dictionaries
        """
        sections = self.sc.get_sections()
        courses = [section.__dict__ for section in sections]

        if sync_to_convex:
            sync_courses(self.convex_url, self.user_id, courses)

        return courses

    def get_assignments(self, course_id: str, sync_to_convex: bool = True) -> list[dict]:
        """
        Fetch assignments for a specific course

        Args:
            course_id: Schoology section/course ID
            sync_to_convex: Whether to sync to Convex cache (default: True)

        Returns:
            List of assignment dictionaries
        """
        assignments = self.sc.get_assignments(course_id)
        assignment_dicts = [a.__dict__ for a in assignments]

        if sync_to_convex:
            sync_assignments(self.convex_url, self.user_id, course_id, assignment_dicts)

        return assignment_dicts

    def get_upcoming_assignments(self, days: int = 7) -> list[dict]:
        """
        Get assignments due within the next N days across all courses

        Args:
            days: Number of days to look ahead (default: 7)

        Returns:
            List of assignment dictionaries with course info, sorted by due date
        """
        # Get all sections
        sections = self.sc.get_sections()

        # Calculate cutoff date
        cutoff = datetime.now() + timedelta(days=days)
        upcoming = []

        # Fetch assignments for each section
        for section in sections:
            try:
                assignments = self.sc.get_assignments(section.id)

                for assignment in assignments:
                    # Parse due date
                    due_date_str = getattr(assignment, 'due', None)
                    if not due_date_str:
                        continue

                    try:
                        # Schoology date format is typically YYYY-MM-DD HH:MM:SS
                        due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        # Try alternate format
                        try:
                            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
                        except ValueError:
                            # Skip if we can't parse the date
                            continue

                    # Check if within range
                    if datetime.now() <= due_date <= cutoff:
                        assignment_dict = assignment.__dict__.copy()
                        # Add course metadata
                        assignment_dict['course_title'] = getattr(section, 'course_title', '')
                        assignment_dict['section_id'] = section.id
                        upcoming.append(assignment_dict)

            except Exception as e:
                print(f"[WARNING] Error fetching assignments for section {section.id}: {e}")
                continue

        # Sort by due date
        upcoming.sort(key=lambda x: x.get('due', ''))

        # Sync to Convex
        sync_upcoming(self.convex_url, self.user_id, upcoming)

        return upcoming

    def refresh_all(self) -> dict:
        """
        Refresh all courses and their assignments in Convex cache

        Returns:
            Dictionary with success status and counts
        """
        # Fetch and sync courses
        courses = self.get_courses(sync_to_convex=True)

        # Fetch and sync assignments for each course
        total_assignments = 0
        for course in courses:
            try:
                assignments = self.get_assignments(course['id'], sync_to_convex=True)
                total_assignments += len(assignments)
            except Exception as e:
                print(f"[WARNING] Error refreshing assignments for course {course['id']}: {e}")
                continue

        return {
            "success": True,
            "courses_updated": len(courses),
            "assignments_updated": total_assignments
        }

    def disconnect(self):
        """
        Clear user's Convex cache (called when user disconnects Schoology)
        """
        clear_cache(self.convex_url, self.user_id)

    def get_user_info(self) -> dict:
        """
        Get current Schoology user information

        Returns:
            Dictionary with user info
        """
        user_data = self.sc.get_me()
        return {
            "id": user_data.uid,
            "name": getattr(user_data, 'name_display', ''),
            "email": getattr(user_data, 'primary_email', '')
        }
