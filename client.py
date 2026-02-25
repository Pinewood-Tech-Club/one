"""
Schoology API client wrapper
"""
import schoolopy
import requests_oauthlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from .convex_sync import sync_courses, sync_assignments, sync_upcoming, sync_profile_picture, clear_cache


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

    def __init__(self, user_id: str, access_token: str | None, access_token_secret: str | None,
                 consumer_key: str, consumer_secret: str, convex_url: str,
                 schoology_domain: str = "https://app.schoology.com",
                 schoology_api_domain: str = "https://api.schoology.com"):
        """
        Initialize Schoology service

        Args:
            user_id: User ID string
            access_token: OAuth access token (three-legged) or None (two-legged)
            access_token_secret: OAuth access token secret (three-legged) or None (two-legged)
            consumer_key: Schoology consumer key
            consumer_secret: Schoology consumer secret
            convex_url: Convex deployment URL
            schoology_domain: Schoology domain URL (default: https://app.schoology.com)
            schoology_api_domain: Schoology API domain (default: https://api.schoology.com)
        """
        self.user_id = user_id
        self.convex_url = convex_url
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret

        three_legged = bool(access_token and access_token_secret)

        # Create schoolopy auth object
        auth = schoolopy.Auth(
            consumer_key,
            consumer_secret,
            three_legged=three_legged,
            domain=schoology_domain,
            access_token=access_token if three_legged else None,
            access_token_secret=access_token_secret if three_legged else None,
        )

        # Ensure we always sign requests using PLAINTEXT (Schoology's OAuth 1.0 requirement).
        # For two-legged auth, omit resource owner tokens entirely.
        auth.oauth = requests_oauthlib.OAuth1Session(
            consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=access_token if three_legged else None,
            resource_owner_secret=access_token_secret if three_legged else None,
            signature_method="PLAINTEXT",
        )

        api_root = schoology_api_domain.rstrip("/")
        if api_root.endswith("/v1"):
            api_host = f"{api_root}/"
        elif api_root.endswith("/v1/"):
            api_host = api_root
        else:
            api_host = f"{api_root}/v1/"

        api_netloc = urlparse(api_host).netloc

        # schoolopy hardcodes Host: api.schoology.com, which can break when using a custom API domain.
        def _request_header() -> dict:
            return {
                "Accept": "application/json",
                "Content-Type": "application/json",
                **({"Host": api_netloc} if api_netloc else {}),
            }

        auth._request_header = _request_header

        self.sc = schoolopy.Schoology(auth, api_host=api_host)

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

    @staticmethod
    def _parse_due_datetime(due_raw) -> datetime | None:
        """
        Parse Schoology due date values into UTC-aware datetimes.
        Supports common formats: YYYY-MM-DD HH:MM:SS, YYYY-MM-DD, and ISO 8601.
        """
        if due_raw is None:
            return None

        # Some APIs return epoch timestamps.
        if isinstance(due_raw, (int, float)):
            try:
                return datetime.fromtimestamp(float(due_raw), tz=timezone.utc)
            except Exception:
                return None

        due_str = str(due_raw).strip()
        if not due_str:
            return None

        local_tz = datetime.now().astimezone().tzinfo

        # Try ISO-style parsing first (covers timezone offsets and fractional seconds).
        iso_candidate = due_str.replace("Z", "+00:00")
        if " " in iso_candidate and "T" not in iso_candidate:
            iso_candidate = iso_candidate.replace(" ", "T", 1)
        try:
            parsed = datetime.fromisoformat(iso_candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

        # Fallback to known Schoology legacy formats.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(due_str, fmt).replace(tzinfo=local_tz)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                continue

        return None

    def get_upcoming_assignments(self, days: int = 365) -> list[dict]:
        """
        Get assignments due within the next N days across all courses

        Args:
            days: Number of days to look ahead (default: 7)

        Returns:
            List of assignment dictionaries with course info, sorted by due date
        """
        # Get all sections
        sections = self.sc.get_sections()

        now_utc = datetime.now(timezone.utc)
        cutoff_utc = now_utc + timedelta(days=days)
        upcoming: list[tuple[datetime, dict]] = []
        total_seen = 0
        missing_due = 0
        parse_failed = 0
        out_of_window = 0

        # Fetch assignments for each section
        for section in sections:
            try:
                assignments = self.sc.get_assignments(section.id)

                for assignment in assignments:
                    total_seen += 1
                    # Parse due date
                    due_date_str = getattr(assignment, 'due', None)
                    if not due_date_str:
                        missing_due += 1
                        continue

                    due_date = self._parse_due_datetime(due_date_str)
                    if not due_date:
                        parse_failed += 1
                        continue

                    # Check if within range
                    if now_utc <= due_date <= cutoff_utc:
                        assignment_dict = assignment.__dict__.copy()
                        # Add course metadata
                        assignment_dict['course_title'] = getattr(section, 'course_title', '')
                        assignment_dict['section_id'] = section.id
                        assignment_dict['section_title'] = getattr(section, 'section_title', '')
                        upcoming.append((due_date, assignment_dict))
                    else:
                        out_of_window += 1

            except Exception as e:
                print(f"[WARNING] Error fetching assignments for section {section.id}: {e}")
                continue

        # Sort by due date
        upcoming.sort(key=lambda x: x[0])
        upcoming_assignments = [assignment for _, assignment in upcoming]

        print(
            f"[DEBUG] Upcoming filter summary user={self.user_id} total={total_seen} "
            f"included={len(upcoming_assignments)} missing_due={missing_due} "
            f"parse_failed={parse_failed} out_of_window={out_of_window} "
            f"window_start={now_utc.isoformat()} window_end={cutoff_utc.isoformat()}"
        )

        # Sync to Convex
        sync_upcoming(self.convex_url, self.user_id, upcoming_assignments)

        return upcoming_assignments

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

        # Fetch and sync upcoming assignments
        try:
            upcoming = self.get_upcoming_assignments()  # Uses default of 365 days
            upcoming_count = len(upcoming)
        except Exception as e:
            print(f"[WARNING] Error refreshing upcoming assignments: {e}")
            upcoming_count = 0

        # Refresh profile picture (lightweight /users/me call)
        try:
            user_info = self.get_user_info()
            if user_info.get("picture_url"):
                sync_profile_picture(self.convex_url, self.user_id, user_info["picture_url"])
        except Exception as e:
            print(f"[WARNING] Error refreshing profile picture: {e}")

        return {
            "success": True,
            "courses_updated": len(courses),
            "assignments_updated": total_assignments,
            "upcoming_updated": upcoming_count
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
        try:
            user_data = self.sc.get_me()
            return {
                "id": user_data.uid,
                "name": getattr(user_data, 'name_display', ''),
                "email": getattr(user_data, 'primary_email', ''),
                "picture_url": getattr(user_data, 'picture_url', None),
            }
        except Exception:
            # Two-legged auth may not support /users/me; fall back to /app-user-info then /users/{api_uid}.
            session = self.sc.get_self_user_info()
            api_uid = getattr(session, "api_uid", None) or session.get("api_uid")
            if not api_uid:
                raise

            user_data = self.sc.get_user(api_uid)
            return {
                "id": getattr(user_data, "uid", api_uid),
                "name": getattr(user_data, 'name_display', ''),
                "email": getattr(user_data, 'primary_email', ''),
                "picture_url": getattr(user_data, 'picture_url', None),
            }
