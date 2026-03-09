#!/usr/bin/env python3
"""
Convert Signal HTML export to Markdown format.
Preserves timestamps, dates, attachments, and replies while discarding CSS styling.
Creates one file per day with frontmatter, organized by year.
"""

from bs4 import BeautifulSoup, Tag
import re
import sys
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def extract_message_id(comment: Any) -> Optional[str]:
    """Extract message ID from HTML comment."""
    match = re.search(r"_id:(\d+)", str(comment))
    return match.group(1) if match else None


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse date string to datetime object."""
    try:
        return datetime.strptime(date_str, "%b %d, %Y")
    except Exception:
        return None


def format_timestamp(timestamp_str: Optional[str]) -> str:
    """Format Signal timestamp as HH:MM:SS."""
    raw = timestamp_str or ""
    match = re.search(r"\b(\d{2}):(\d{2})(?::(\d{2}))?$", raw)
    if match:
        seconds = match.group(3) if match.group(3) else "00"
        return f"{match.group(1)}:{match.group(2)}:{seconds}"
    return raw


def process_message(msg_div: Tag) -> dict[str, Any]:
    """Process a single message div and extract relevant information."""
    result: dict[str, Any] = {}

    # Get message classes
    classes_raw = msg_div.get("class", None)
    classes = (
        classes_raw
        if isinstance(classes_raw, list)
        else [classes_raw] if classes_raw else []
    )
    result["classes"] = classes

    # Determine message type
    if "msg-date-change" in classes:
        date_p = msg_div.find("p")
        result["type"] = "date"
        result["content"] = date_p.get_text(strip=True) if date_p else ""
        return result

    if "msg-status" in classes:
        status_text = msg_div.find("pre")
        footer = msg_div.find("span", class_="msg-data")
        result["type"] = "status"
        result["content"] = status_text.get_text(strip=True) if status_text else ""
        result["timestamp"] = footer.get_text(strip=True) if footer else ""
        return result

    # Regular message (incoming/outgoing)
    result["type"] = "outgoing" if "msg-outgoing" in classes else "incoming"

    # Extract timestamp
    footer = msg_div.find("span", class_="msg-data")
    result["timestamp"] = footer.get_text(strip=True) if footer else ""

    # Extract attachments
    attachments = []
    for att_div in msg_div.find_all("div", class_="attachment"):
        img = att_div.find("img")
        if img and img.get("src"):
            attachments.append(img.get("src"))
    result["attachments"] = attachments

    # Extract quoted/replied message
    quote = msg_div.find("div", class_="msg-quote")
    if quote:
        quote_msg = quote.find("div", class_="msg-quote-message")
        if quote_msg:
            quote_text = quote_msg.find("pre")
            result["reply_to"] = quote_text.get_text(strip=True) if quote_text else ""

    # Extract message content (excluding quotes and attachments)
    content_parts = []
    for child in msg_div.children:
        if isinstance(child, str):
            continue
        if not isinstance(child, Tag):
            continue
        child_classes = child.get("class", None)
        child_classes_list = (
            child_classes
            if isinstance(child_classes, list)
            else [child_classes] if child_classes else []
        )
        if (
            child.name == "div"
            and "attachment" not in child_classes_list
            and "msg-quote" not in child_classes_list
            and "footer" not in child_classes_list
            and "edited" not in child_classes_list
        ):
            pre_tag = child.find("pre")
            if pre_tag:
                # Get text and preserve links
                text = ""
                for element in pre_tag.descendants:
                    if isinstance(element, str):
                        text += element
                    elif isinstance(element, Tag) and element.name == "a":
                        href_raw = element.get("href", "")
                        href = str(href_raw) if href_raw else ""
                        link_text = element.get_text()
                        if href == link_text:
                            text += href
                        else:
                            text += f"[{link_text}]({href})"
                content_parts.append(text.strip())

    result["content"] = "\n".join(content_parts)

    # Check if message was edited
    edited_div = msg_div.find("div", class_="edited")
    if edited_div:
        result["edited"] = True

    return result


def convert_html_to_markdown(html_path: str, output_dir: str) -> int:
    """Convert Signal HTML export to Markdown, one file per day organized by year."""

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, "html.parser")

    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")

    # Find the conversation box
    conversation_box = soup.find("div", class_="conversation-box")
    if not conversation_box:
        print("Error: Could not find conversation box")
        return 0

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Get the source media directory
    html_dir = os.path.dirname(html_path)

    # Track copied attachments per year
    copied_attachments: dict[tuple[int, str], Optional[str]] = {}

    # Process all messages grouped by date
    daily_messages: dict[str, list[dict[str, Any]]] = {}
    current_date: Optional[datetime] = None
    current_date_str: Optional[str] = None

    for element in conversation_box.children:
        # Process message divs
        if isinstance(element, Tag) and element.name == "div":
            element_classes = element.get("class", None)
            element_classes_list = (
                element_classes
                if isinstance(element_classes, list)
                else [element_classes] if element_classes else []
            )
            if "msg" in element_classes_list:
                msg_data = process_message(element)

                if msg_data["type"] == "date":
                    current_date_str = msg_data["content"]
                    if current_date_str:
                        current_date = parse_date(current_date_str)
                        if current_date and current_date_str not in daily_messages:
                            daily_messages[current_date_str] = []

                elif current_date_str:
                    daily_messages[current_date_str].append(msg_data)

    # Write one file per day, organized by year
    files_written = 0
    attachments_per_year: dict[int, set[str]] = {}

    for date_str, messages in daily_messages.items():
        date_obj = parse_date(date_str)
        if not date_obj:
            continue

        # Create year folder and its attachments subfolder
        year_folder = os.path.join(output_dir, str(date_obj.year))
        Path(year_folder).mkdir(parents=True, exist_ok=True)

        year_attachments_dir = os.path.join(year_folder, "attachments")
        Path(year_attachments_dir).mkdir(parents=True, exist_ok=True)

        # Track attachments for this year
        if date_obj.year not in attachments_per_year:
            attachments_per_year[date_obj.year] = set()

        # Create filename: YYYY-MM-DD.md
        filename = date_obj.strftime("%Y-%m-%d") + ".md"
        output_path = os.path.join(year_folder, filename)

        # Build markdown content
        markdown_lines = []

        # Add frontmatter with only requested fields
        markdown_lines.append("---\n")
        markdown_lines.append("mood: \n")
        markdown_lines.append("location: null\n")
        markdown_lines.append("tags:\n")
        markdown_lines.append(f"created: '{created_utc}'\n")
        markdown_lines.append("---\n\n")

        # Add messages
        for msg_data in messages:
            if msg_data["type"] == "status":
                # Status message
                markdown_lines.append(f'*{msg_data["content"]}*')
                if msg_data["timestamp"]:
                    markdown_lines.append(
                        f' %% {format_timestamp(msg_data["timestamp"])} %%'
                    )
                markdown_lines.append("\n\n")

            else:
                # Regular message
                # Add timestamp as markdown comment
                if msg_data["timestamp"]:
                    markdown_lines.append(
                        f'%% {format_timestamp(msg_data["timestamp"])} %%\n\n'
                    )

                # Add reply/quote
                if "reply_to" in msg_data:
                    markdown_lines.append(f'> {msg_data["reply_to"]}\n\n')

                # Add attachments as images
                for att in msg_data["attachments"]:
                    # Copy attachment to year's attachments folder if not already copied
                    attachment_key = (date_obj.year, att)
                    if attachment_key not in copied_attachments:
                        source_path = os.path.join(html_dir, att)
                        if os.path.exists(source_path):
                            filename_only = os.path.basename(att)
                            dest_path = os.path.join(
                                year_attachments_dir, filename_only
                            )
                            try:
                                shutil.copy2(source_path, dest_path)
                                copied_attachments[attachment_key] = filename_only
                                attachments_per_year[date_obj.year].add(filename_only)
                                print(
                                    f"  Copied to {date_obj.year}/attachments/: {filename_only}"
                                )
                            except Exception as e:
                                print(f"  Warning: Could not copy {att}: {e}")
                                copied_attachments[attachment_key] = None
                        else:
                            print(f"  Warning: Source file not found: {source_path}")
                            copied_attachments[attachment_key] = None

                    # Use relative path to attachments folder in same year
                    attachment_filename = copied_attachments.get(attachment_key)
                    if attachment_filename:
                        rel_path = os.path.join("attachments", attachment_filename)
                        markdown_lines.append(f"![attachment]({rel_path})\n\n")

                # Add message content
                if msg_data["content"]:
                    markdown_lines.append(f'{msg_data["content"]}')
                    if msg_data.get("edited"):
                        markdown_lines.append(" *(edited)*")
                    markdown_lines.append("\n\n")
                elif msg_data["attachments"] and not msg_data["content"]:
                    # Just attachment, no need for extra line
                    pass

        # Write to file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("".join(markdown_lines))

        files_written += 1

    print("\nConversion complete!")
    print(f"  {files_written} markdown files written to: {output_dir}")
    total_attachments: int = sum(
        len(att_set) for att_set in attachments_per_year.values()
    )
    print(
        f"  {total_attachments} attachments copied across {len(attachments_per_year)} year folders"
    )
    for year in sorted(attachments_per_year.keys()):
        print(f"    {year}/attachments/: {len(attachments_per_year[year])} files")
    return files_written


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python html_to_markdown.py <input_html_file> <output_directory>")
        print("\nExample:")
        print("  python html_to_markdown.py html/self.html output_messages")
        print("\nThis will create:")
        print("  - Year folders (2019/, 2022/, etc.) with markdown files")
        print("  - attachments/ folder with all media files")
        sys.exit(1)

    html_file = sys.argv[1]
    output_dir = sys.argv[2]

    # Verify input file exists
    if not os.path.exists(html_file):
        print(f"Error: Input file not found: {html_file}")
        sys.exit(1)

    print(f"Converting {html_file} to {output_dir}/...")
    print("-" * 60)
    convert_html_to_markdown(html_file, output_dir)
