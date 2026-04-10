"""LinkedIn CLI — manual testing interface for the LinkedIn tool.

Usage:
    python cli.py profile --public-id john-doe-123
    python cli.py search-people --keywords "software engineer"
    python cli.py search-jobs --keywords "python developer" --location "New York"
    python cli.py job --job-id 12345678
    python cli.py conversations
    python cli.py send --to john-doe-123 --message "Hello!"
    python cli.py invitations
    python cli.py my-profile
    python cli.py views

Credentials are read from environment variables:
    TOOL_LINKEDIN_EMAIL    — LinkedIn account email
    TOOL_LINKEDIN_PASSWORD — LinkedIn account password
"""

import argparse
import json
import os
import sys


def _get_client():
    """Create LinkedIn client from env vars."""
    try:
        from linkedin_api import Linkedin
    except ImportError:
        print("Error: linkedin-api not installed. pip install linkedin-api", file=sys.stderr)
        sys.exit(1)

    email = os.environ.get("TOOL_LINKEDIN_EMAIL", "")
    password = os.environ.get("TOOL_LINKEDIN_PASSWORD", "")

    if not email or not password:
        print(
            "Error: Set TOOL_LINKEDIN_EMAIL and TOOL_LINKEDIN_PASSWORD env vars.",
            file=sys.stderr,
        )
        sys.exit(1)

    return Linkedin(email, password)


def cmd_profile(args):
    api = _get_client()
    profile = api.get_profile(args.public_id)
    contact = api.get_profile_contact_info(args.public_id)
    skills = api.get_profile_skills(args.public_id)
    result = {
        "name": f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip(),
        "headline": profile.get("headline", ""),
        "summary": profile.get("summary", ""),
        "location": profile.get("locationName", ""),
        "experience_count": len(profile.get("experience", [])),
        "skills": [s.get("name", "") for s in (skills or [])[:15]],
        "email": contact.get("email_address", ""),
    }
    json.dump(result, sys.stdout, indent=2)


def cmd_my_profile(args):
    api = _get_client()
    profile = api.get_user_profile()
    json.dump(profile, sys.stdout, indent=2, default=str)


def cmd_views(args):
    api = _get_client()
    views = api.get_current_profile_views()
    json.dump(views, sys.stdout, indent=2, default=str)


def cmd_search_people(args):
    api = _get_client()
    results = api.search_people(keywords=args.keywords, limit=args.limit)
    for p in results[: args.limit]:
        print(f"  {p.get('name', '?'):30s} | {p.get('jobtitle', '')}")


def cmd_search_jobs(args):
    api = _get_client()
    kwargs = {"keywords": args.keywords, "limit": args.limit}
    if args.location:
        kwargs["location_name"] = args.location
    results = api.search_jobs(**kwargs)
    for j in results[: args.limit]:
        title = j.get("title", "?")
        company = j.get("companyName", "?")
        loc = j.get("formattedLocation", "")
        print(f"  {title:40s} | {company:25s} | {loc}")


def cmd_job(args):
    api = _get_client()
    job = api.get_job(args.job_id)
    json.dump(job, sys.stdout, indent=2, default=str)


def cmd_conversations(args):
    api = _get_client()
    convos = api.get_conversations()
    elements = convos.get("elements", []) if isinstance(convos, dict) else convos
    for c in elements[:10]:
        participants = []
        for p in c.get("participants", []):
            mini = p.get("com.linkedin.voyager.messaging.MessagingMember", {}).get("miniProfile", {})
            participants.append(f"{mini.get('firstName', '')} {mini.get('lastName', '')}".strip())
        print(f"  {', '.join(participants):40s} | {c.get('entityUrn', '')}")


def cmd_send(args):
    api = _get_client()
    profile = api.get_profile(args.to)
    profile_urn = profile.get("profile_id", "")
    if not profile_urn:
        print("Error: Could not resolve profile URN", file=sys.stderr)
        sys.exit(1)
    err = api.send_message(message_body=args.message, recipients=[profile_urn])
    if err:
        print("Error: Message send failed", file=sys.stderr)
        sys.exit(1)
    print(f"Message sent to {args.to}")


def cmd_invitations(args):
    api = _get_client()
    invitations = api.get_invitations()
    for inv in (invitations or [])[:20]:
        fm = inv.get("fromMember", {})
        name = f"{fm.get('firstName', '')} {fm.get('lastName', '')}".strip()
        print(f"  {name:30s} | {fm.get('occupation', '')}")


def main():
    parser = argparse.ArgumentParser(description="LinkedIn CLI tool")
    sub = parser.add_subparsers(dest="command", required=True)

    # profile
    p = sub.add_parser("profile", help="Get a user's profile")
    p.add_argument("--public-id", required=True, help="LinkedIn public ID")
    p.set_defaults(func=cmd_profile)

    # my-profile
    p = sub.add_parser("my-profile", help="Get your own profile")
    p.set_defaults(func=cmd_my_profile)

    # views
    p = sub.add_parser("views", help="Get your profile view stats")
    p.set_defaults(func=cmd_views)

    # search-people
    p = sub.add_parser("search-people", help="Search for people")
    p.add_argument("--keywords", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search_people)

    # search-jobs
    p = sub.add_parser("search-jobs", help="Search for jobs")
    p.add_argument("--keywords", required=True)
    p.add_argument("--location", default="")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search_jobs)

    # job
    p = sub.add_parser("job", help="Get job details")
    p.add_argument("--job-id", required=True)
    p.set_defaults(func=cmd_job)

    # conversations
    p = sub.add_parser("conversations", help="List message conversations")
    p.set_defaults(func=cmd_conversations)

    # send
    p = sub.add_parser("send", help="Send a message")
    p.add_argument("--to", required=True, help="Recipient public ID")
    p.add_argument("--message", required=True, help="Message text")
    p.set_defaults(func=cmd_send)

    # invitations
    p = sub.add_parser("invitations", help="List pending invitations")
    p.set_defaults(func=cmd_invitations)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
