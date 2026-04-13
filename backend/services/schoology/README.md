# Schoology Service

Python package for Schoology API wrapper and Convex cache synchronization.

## Overview

This package provides a clean interface to:
- Interact with the Schoology API using OAuth authentication
- Automatically synchronize Schoology data (courses, assignments) to Convex cache
- Handle OAuth flows for Schoology authentication

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### OAuth Flow

```python
from services.schoology import start_oauth, complete_oauth

# Step 1: Start OAuth flow
auth_url, request_token, request_token_secret = start_oauth(
    consumer_key="your_key",
    consumer_secret="your_secret",
    callback_url="https://yourapp.com/callback"
)

# Store request_token and request_token_secret, redirect user to auth_url

# Step 2: Complete OAuth (in your callback handler)
access_token, access_token_secret = complete_oauth(
    consumer_key="your_key",
    consumer_secret="your_secret",
    request_token=request_token,
    request_token_secret=request_token_secret
)

# Store access_token and access_token_secret for the user
```

### Using the Schoology Service

```python
from services.schoology import SchoologyService

# Initialize the service
service = SchoologyService(
    user_id="123",
    access_token="user_access_token",
    access_token_secret="user_access_token_secret",
    consumer_key="your_consumer_key",
    consumer_secret="your_consumer_secret",
    convex_url="https://your-convex-deployment.convex.cloud"
)

# Fetch courses (automatically syncs to Convex)
courses = service.get_courses()

# Fetch assignments for a course
assignments = service.get_assignments(course_id="12345")

# Get upcoming assignments (within next 7 days, computed from Schoology API)
upcoming = service.get_upcoming_assignments(days=7)

# Refresh all data in Convex cache
result = service.refresh_all()

# Clear user's cache when disconnecting
service.disconnect()
```

## Features

- **Automatic Convex Sync**: All data fetching methods automatically update the Convex cache
- **Upcoming Assignments**: Smart filtering of assignments due within a specified timeframe
- **Error Handling**: Graceful handling of API errors and date parsing issues
- **Stateless OAuth**: OAuth helpers don't require database access

## Requirements

- Python 3.8+
- schoolopy (Schoology API wrapper)
- convex (Convex Python client)
- requests-oauthlib (OAuth 1.0a)

## License

Private - Pinewood Tech Club
