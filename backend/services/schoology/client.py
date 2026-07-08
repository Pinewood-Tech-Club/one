"""
Schoology API client wrapper
"""
import ipaddress
import json
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin, urlparse
from xml.sax.saxutils import escape

import requests
import requests_oauthlib
import schoolopy

from .convex_sync import (
    clear_cache,
    sync_course_assignments,
    sync_courses,
    sync_profile_picture,
)

# SSRF guard settings for outbound binary downloads (attachment URLs come from
# scraped third-party data and must not be trusted blindly).
_ALLOWED_DOWNLOAD_SCHEMES = {"http", "https"}
_MAX_DOWNLOAD_REDIRECTS = 5


def validate_outbound_url(url: str) -> None:
    """
    Validate that a URL is safe to fetch server-side (SSRF guard).

    Raises ValueError unless the URL uses http/https and its hostname resolves
    only to public (global) IP addresses — blocking loopback, RFC1918 private,
    link-local, CGNAT, multicast, and other reserved ranges for IPv4 and IPv6.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_DOWNLOAD_SCHEMES:
        raise ValueError(f"Blocked outbound request with disallowed scheme {parsed.scheme!r}")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Blocked outbound request without a hostname")

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        address_infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Blocked outbound request; could not resolve host {hostname!r}") from exc

    for info in address_infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError(
                f"Blocked outbound request to non-public address {ip} for host {hostname!r}"
            )


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
        self.access_token = access_token
        self.access_token_secret = access_token_secret

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

    @staticmethod
    def _chunked(items: list[str], chunk_size: int) -> list[list[str]]:
        return [
            items[index:index + chunk_size]
            for index in range(0, len(items), chunk_size)
        ]

    @staticmethod
    def _normalize_api_path(path: str) -> str:
        normalized = str(path).strip()
        if normalized.startswith("/v1/"):
            return normalized[4:].lstrip("/")
        return normalized.lstrip("/")

    def _build_api_url(self, path: str, params: dict | None = None) -> str:
        url = f"{self.sc.api_host.rstrip('/')}/{self._normalize_api_path(path)}"
        if params:
            query = urlencode(params, doseq=True)
            if query:
                url = f"{url}?{query}"
        return url

    def _request_schoology(self, method: str, path: str, *, params: dict | None = None,
                           data: str | bytes | None = None, headers: dict | None = None):
        request_headers = self.sc.schoology_auth._request_header().copy()
        if headers:
            request_headers.update(headers)

        session = self.sc.schoology_auth.oauth
        response = session.request(
            method=method.upper(),
            url=self._build_api_url(path, params=params),
            data=data,
            headers=request_headers,
            auth=session.auth,
        )
        response.raise_for_status()

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError:
            return response.text

    def _download_schoology_binary(self, url: str) -> tuple[bytes, str | None]:
        resolved_url = url if urlparse(url).scheme else urljoin(self.sc.api_host, url)
        api_netloc = urlparse(self.sc.api_host).netloc.lower()

        # Follow redirects manually so every hop is re-validated against the
        # SSRF guard (Schoology download URLs redirect to presigned CDN/S3
        # hosts, so a fixed host allowlist is not viable here).
        current_url = resolved_url
        for _ in range(_MAX_DOWNLOAD_REDIRECTS + 1):
            validate_outbound_url(current_url)

            # Only sign requests to our own Schoology API host; never send
            # OAuth credentials to redirect targets on other hosts.
            auth = None
            if urlparse(current_url).netloc.lower() == api_netloc:
                auth = requests_oauthlib.OAuth1(
                    self.consumer_key,
                    client_secret=self.consumer_secret,
                    resource_owner_key=self.access_token,
                    resource_owner_secret=self.access_token_secret,
                    signature_method="PLAINTEXT",
                    signature_type="auth_header",
                )

            response = requests.get(
                current_url,
                auth=auth,
                timeout=60,
                allow_redirects=False,
            )

            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError(f"Redirect without Location header from {current_url!r}")
                current_url = urljoin(current_url, location)
                continue

            response.raise_for_status()
            return response.content, response.headers.get("Content-Type")

        raise ValueError(f"Too many redirects while downloading {resolved_url!r}")

    def _multiget(self, request_paths: list[str]) -> list[dict]:
        body = ["<?xml version=\"1.0\" encoding=\"utf-8\" ?>", "<requests>"]
        for path in request_paths:
            body.append(f"  <request>{escape(path)}</request>")
        body.append("</requests>")

        response = self._request_schoology(
            "POST",
            "multiget",
            data="\n".join(body).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "text/xml; charset=utf-8",
            },
        )

        if isinstance(response, list):
            items = response
        elif isinstance(response, dict):
            items = None
            for key in ("responses", "response", "results", "items"):
                value = response.get(key)
                if isinstance(value, list):
                    items = value
                    break
                if isinstance(value, dict):
                    items = [value]
                    break
            if items is None:
                raise ValueError("Unexpected Multi-GET response envelope")
        else:
            raise ValueError("Multi-GET did not return JSON")

        payloads: list[dict] = []
        for item in items:
            payload = item
            if isinstance(item, dict):
                status_code = self._coerce_positive_int(
                    item.get("status")
                    or item.get("status_code")
                    or item.get("code")
                )
                if status_code is not None and status_code >= 400:
                    raise ValueError(f"Multi-GET item returned HTTP {status_code}")
                if "assignment" in item:
                    payload = item
                else:
                    for key in ("body", "data", "result", "response", "payload"):
                        if key not in item:
                            continue
                        candidate = item[key]
                        if isinstance(candidate, dict):
                            payload = candidate
                            break
                        if isinstance(candidate, str):
                            stripped = candidate.strip()
                            if not stripped:
                                payload = {}
                                break
                            try:
                                payload = json.loads(stripped)
                                break
                            except ValueError as exc:
                                raise ValueError("Multi-GET body was not JSON") from exc

            if not isinstance(payload, dict):
                raise ValueError("Multi-GET item did not contain a JSON object payload")
            payloads.append(payload)

        return payloads

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

    def get_sections(self, sync_to_convex: bool = False) -> list[dict]:
        """
        Fetch sections visible to the current credential owner.
        """
        return self.get_courses(sync_to_convex=sync_to_convex)

    def get_assignments(
        self,
        course_id: str,
        sync_to_convex: bool = True,
        *,
        with_attachments: bool = False,
    ) -> list[dict]:
        """
        Fetch assignments for a specific course

        Args:
            course_id: Schoology section/course ID
            sync_to_convex: Whether to sync to Convex cache (default: True)

        Returns:
            List of assignment dictionaries
        """
        assignment_dicts = self._fetch_section_assignments_paginated(
            course_id,
            with_attachments=with_attachments,
        )

        if sync_to_convex:
            sync_course_assignments(self.convex_url, self.user_id, str(course_id), assignment_dicts)

        return assignment_dicts

    @staticmethod
    def _coerce_positive_int(value) -> int | None:
        """Best-effort conversion to a non-negative integer."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    def _extract_assignment_page(self, payload) -> tuple[list[dict], int | None, int | None]:
        if not isinstance(payload, dict):
            raise ValueError("Assignments payload was not a JSON object")

        raw_assignments = payload.get("assignment", [])
        if isinstance(raw_assignments, dict):
            raw_assignments = [raw_assignments]
        elif raw_assignments is None:
            raw_assignments = []
        elif not isinstance(raw_assignments, list):
            raise ValueError("Assignments payload contained an invalid assignment field")

        total = self._coerce_positive_int(payload.get("total"))
        count = self._coerce_positive_int(payload.get("count"))
        return raw_assignments, total, count

    def _merge_assignment_page(self, state: dict, payload) -> bool:
        raw_assignments, total, count = self._extract_assignment_page(payload)
        start = state["start"]

        if not raw_assignments:
            return True

        page_new = 0
        for raw in raw_assignments:
            if not isinstance(raw, dict):
                continue
            assignment_id = raw.get("id") or raw.get("grade_item_id")
            dedupe_key = str(assignment_id) if assignment_id is not None else f"fallback:{start}:{page_new}"
            if dedupe_key in state["seen_ids"]:
                continue
            state["seen_ids"].add(dedupe_key)
            state["assignments"].append(raw)
            page_new += 1

        page_size = count if count and count > 0 else len(raw_assignments)
        next_start = start + page_size

        if total is not None and next_start >= total:
            return True
        if next_start <= start:
            return True
        if page_new == 0:
            return True

        state["start"] = next_start
        return False

    def _build_assignment_fetch_state(self) -> dict:
        return {
            "start": 0,
            "assignments": [],
            "seen_ids": set(),
            "page_count": 0,
        }

    def _fetch_section_assignments_paginated(
        self,
        section_id: str | int,
        page_limit: int = 200,
        *,
        with_attachments: bool = False,
    ) -> list[dict]:
        """
        Fetch all assignments for a section by paging Schoology's assignments endpoint.
        """
        max_pages = 500  # Safety guard against malformed pagination metadata.
        state = self._build_assignment_fetch_state()

        while state["page_count"] < max_pages:
            payload = self._request_schoology(
                "GET",
                f"sections/{section_id}/assignments",
                params={
                    "start": state["start"],
                    "limit": page_limit,
                    **({"with_attachments": 1} if with_attachments else {}),
                },
            )
            state["page_count"] += 1
            if self._merge_assignment_page(state, payload):
                break

        print(
            f"[DEBUG] Assignment pagination summary user={self.user_id} section={section_id} "
            f"pages={state['page_count']} total_rows={len(state['assignments'])}"
        )
        return state["assignments"]

    def _fetch_assignments_for_sections(
        self,
        section_ids: list[str | int],
        page_limit: int = 200,
        *,
        with_attachments: bool = False,
    ) -> dict[str, list[dict]]:
        normalized_ids = [
            str(section_id)
            for section_id in section_ids
            if section_id is not None and str(section_id)
        ]
        if not normalized_ids:
            return {}

        max_pages = 500
        states = {
            section_id: self._build_assignment_fetch_state()
            for section_id in normalized_ids
        }
        completed: dict[str, list[dict]] = {}
        pending = normalized_ids[:]

        while pending:
            next_pending: list[str] = []
            for chunk in self._chunked(pending, 50):
                request_paths = [
                    f"/v1/sections/{section_id}/assignments?"
                    f"start={states[section_id]['start']}&limit={page_limit}"
                    f"{'&with_attachments=1' if with_attachments else ''}"
                    for section_id in chunk
                ]

                try:
                    payloads = self._multiget(request_paths)
                    if len(payloads) != len(chunk):
                        raise ValueError(
                            f"Expected {len(chunk)} Multi-GET payloads but received {len(payloads)}"
                        )
                except Exception as exc:
                    print(
                        f"[WARNING] Multi-GET failed for sections {chunk}: {exc}. "
                        "Falling back to per-section requests."
                    )
                    for section_id in chunk:
                        try:
                            completed[section_id] = self._fetch_section_assignments_paginated(
                                section_id,
                                page_limit=page_limit,
                                with_attachments=with_attachments,
                            )
                        except Exception as section_exc:
                            print(
                                f"[WARNING] Error refreshing assignments for course {section_id}: {section_exc}"
                            )
                            completed[section_id] = []
                    continue

                for section_id, payload in zip(chunk, payloads):
                    state = states[section_id]
                    state["page_count"] += 1
                    done = self._merge_assignment_page(state, payload)

                    if done or state["page_count"] >= max_pages:
                        completed[section_id] = state["assignments"]
                        print(
                            f"[DEBUG] Assignment pagination summary user={self.user_id} section={section_id} "
                            f"pages={state['page_count']} total_rows={len(state['assignments'])}"
                        )
                    else:
                        next_pending.append(section_id)

            pending = next_pending

        return completed

    @staticmethod
    def _extract_collection_page(payload, item_key: str) -> tuple[list[dict], int | None, int | None]:
        if not isinstance(payload, dict):
            raise ValueError(f"{item_key} payload was not a JSON object")

        raw_items = payload.get(item_key, [])
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        elif raw_items is None:
            raw_items = []
        elif not isinstance(raw_items, list):
            raise ValueError(f"{item_key} payload contained an invalid {item_key} field")

        total = SchoologyService._coerce_positive_int(payload.get("total"))
        count = SchoologyService._coerce_positive_int(payload.get("count"))
        return raw_items, total, count

    def _merge_collection_page(self, state: dict, payload, *, item_key: str, id_keys: tuple[str, ...]) -> bool:
        raw_items, total, count = self._extract_collection_page(payload, item_key)
        start = state["start"]

        if not raw_items:
            return True

        page_new = 0
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item_id = None
            for key in id_keys:
                value = raw.get(key)
                if value is not None and str(value).strip():
                    item_id = str(value)
                    break
            dedupe_key = item_id or f"fallback:{start}:{page_new}"
            if dedupe_key in state["seen_ids"]:
                continue
            state["seen_ids"].add(dedupe_key)
            state["items"].append(raw)
            page_new += 1

        page_size = count if count and count > 0 else len(raw_items)
        next_start = start + page_size

        if total is not None and next_start >= total:
            return True
        if next_start <= start:
            return True
        if page_new == 0:
            return True

        state["start"] = next_start
        return False

    def _build_collection_fetch_state(self) -> dict:
        return {
            "start": 0,
            "items": [],
            "seen_ids": set(),
            "page_count": 0,
        }

    def _fetch_section_documents_paginated(
        self,
        section_id: str | int,
        page_limit: int = 200,
        *,
        with_attachments: bool = True,
    ) -> list[dict]:
        """
        Fetch all documents/materials for a section by paging the documents endpoint.
        """
        max_pages = 500
        state = self._build_collection_fetch_state()

        while state["page_count"] < max_pages:
            params = {"start": state["start"], "limit": page_limit}
            if with_attachments:
                params["with_attachments"] = 1
            try:
                payload = self._request_schoology(
                    "GET",
                    f"sections/{section_id}/documents",
                    params=params,
                )
            except Exception:
                if with_attachments:
                    payload = self._request_schoology(
                        "GET",
                        f"sections/{section_id}/documents",
                        params={"start": state["start"], "limit": page_limit},
                    )
                else:
                    raise
            state["page_count"] += 1
            if self._merge_collection_page(
                state,
                payload,
                item_key="document",
                id_keys=("id", "document_id"),
            ):
                break

        print(
            f"[DEBUG] Document pagination summary user={self.user_id} section={section_id} "
            f"pages={state['page_count']} total_rows={len(state['items'])}"
        )
        return state["items"]

    def get_documents(
        self,
        section_id: str,
        *,
        with_attachments: bool = True,
    ) -> list[dict]:
        """
        Fetch documents/materials for a section.
        """
        return self._fetch_section_documents_paginated(
            section_id,
            with_attachments=with_attachments,
        )

    def download_attachment_bytes(self, url: str) -> tuple[bytes, str | None]:
        """
        Download an attachment using the current Schoology credential context.
        """
        return self._download_schoology_binary(url)

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

    def get_upcoming_assignments(self, days: int | None = 7) -> list[dict]:
        """
        Get assignments due within the next N days across all courses.

        Args:
            days: Number of days to look ahead. If None, returns all future assignments.

        Returns:
            List of assignment dictionaries with course info, sorted by due date
        """
        # Get all sections
        sections = self.sc.get_sections()
        assignments_by_section = self._fetch_assignments_for_sections([section.id for section in sections])

        now_utc = datetime.now(timezone.utc)
        cutoff_utc = now_utc + timedelta(days=days) if days is not None else None
        upcoming: list[tuple[datetime, dict]] = []
        total_seen = 0
        missing_due = 0
        parse_failed = 0
        out_of_window = 0

        # Fetch assignments for each section
        for section in sections:
            try:
                assignments = assignments_by_section.get(str(section.id), [])

                for assignment_dict in assignments:
                    total_seen += 1
                    # Parse due date
                    due_date_str = assignment_dict.get("due")
                    if not due_date_str:
                        missing_due += 1
                        continue

                    due_date = self._parse_due_datetime(due_date_str)
                    if not due_date:
                        parse_failed += 1
                        continue

                    # Check if within range
                    within_upper_bound = cutoff_utc is None or due_date <= cutoff_utc
                    if due_date >= now_utc and within_upper_bound:
                        assignment_with_meta = assignment_dict.copy()
                        # Add course metadata
                        assignment_with_meta["course_title"] = getattr(section, "course_title", "")
                        assignment_with_meta["section_id"] = section.id
                        assignment_with_meta["section_title"] = getattr(section, "section_title", "")
                        upcoming.append((due_date, assignment_with_meta))
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
            f"window_start={now_utc.isoformat()} "
            f"window_end={(cutoff_utc.isoformat() if cutoff_utc is not None else 'none')}"
        )

        return upcoming_assignments

    def refresh_all(self) -> dict:
        """
        Refresh all courses and their assignments in Convex cache

        Returns:
            Dictionary with success status and counts
        """
        # Fetch and sync courses
        courses = self.get_courses(sync_to_convex=True)
        assignments_by_course = self._fetch_assignments_for_sections([
            course["id"]
            for course in courses
            if course.get("id") is not None
        ])

        # Fetch and sync assignments for each course
        total_assignments = 0
        upcoming_count = 0
        now_utc = datetime.now(timezone.utc)
        for course in courses:
            try:
                course_id = str(course["id"])
                assignments = assignments_by_course.get(course_id, [])
                sync_course_assignments(self.convex_url, self.user_id, course_id, assignments)
                total_assignments += len(assignments)
                for assignment in assignments:
                    due_date = self._parse_due_datetime(assignment.get("due"))
                    if due_date and due_date >= now_utc:
                        upcoming_count += 1
            except Exception as e:
                print(f"[WARNING] Error refreshing assignments for course {course['id']}: {e}")
                continue

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
