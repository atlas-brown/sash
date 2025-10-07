import csv
import sys
import time

import requests

# ----- Filters -----
# Info: https://api.stackexchange.com/docs/create-filter
# Easy filter creation: https://api.stackexchange.com/docs/search
# ! Removing the 'page', 'has_more' and 'is_answered' fields breaks this script

# backoff, items, has_more, page, question_id, title, is_answered, link
FILTER_BACKOFF_ITEMS_HASMORE_PAGE_QUESTIONID_TITLE_ISANSWERED_LINK = (
    "!LhqSNodTju1IrkHiEf27gwBNTKY7GwkOEY"
)

# backoff, items, has_more, page, question_id, title, is_answered, link, body_markdown
FILTER_BACKOFF_ITEMS_HASMORE_PAGE_QUESTIONID_TITLE_ISANSWERED_LINK_BODYMARKDOWN = (
    "!0XsWR.sYq2k-O)fq-YI5mar8cBaSORFSbbj_f"
)

# Add more filters here if needed


# ----- Configurable variables -----

# Info: https://api.stackexchange.com/docs/sites
# For each site in the list, a different request is made
# The sites are searched in the order they are present in the list
SITES = ["stackoverflow"]  # "unix"

# Info: https://stackoverflow.com/tags
# Info: https://unix.stackexchange.com/tags
# All tags are used in every request
# If multiple tags are present, questions that match at least one are returned
TAGS = ["shell", "bash"]

# Each element represents a string (can be multiple words) that must be present in the title
# For each element a different request is made
IN_TITLE = []

# If true, unanswered questions are skipped
# Make sure the filter contains the 'is_answered' field!
SKIP_UNANSWERED = False

# The maximum number of questions to fetch
TOTAL_QUESTIONS = 10

SORT_BY = "relevance"  # "last_activity_date", "creation_date", "score"

ORDER_BY = "desc"  # "asc"

OUTPUT_FILE_NAME = "stackexchange_questions.csv"


# ----- Constants -----

# Info: https://api.stackexchange.com, https://api.stackexchange.com/docs/search
BASE_URL = "https://api.stackexchange.com/2.3/search"


def main():
    global SKIP_UNANSWERED

    questions: list[dict] = []

    if len(IN_TITLE) == 0:
        IN_TITLE.append("") # hack to make the loop below work

    # [(s0, k0), (s0, k1), ..., (sN, k0), ..., (sN, kN)] (meaning site, keyword)
    for site, keyword in [(s, k) for s in SITES for k in IN_TITLE]:
        page = 1
        while TOTAL_QUESTIONS - len(questions) > 0:
            params = {
                "order": ORDER_BY,
                "sort": SORT_BY,
                # 'tagged' accepts a semicolon-delimited list of tags
                # The semicolon acts as an OR
                "tagged": ";".join(TAGS),
                "intitle": keyword,
                "site": site,
                # 'pagesize' can be a number between 0 and 100
                "pagesize": calculate_page_size(TOTAL_QUESTIONS, len(questions)),
                "page": page,
                # 'filter' specifies which fields to return
                # See FILTER_* constants above
                "filter": FILTER_BACKOFF_ITEMS_HASMORE_PAGE_QUESTIONID_TITLE_ISANSWERED_LINK,
            }

            response = requests.get(BASE_URL, params=params)
            if not response.ok:
                print(f"{response.status_code}: {response.reason}", file=sys.stderr)
                sys.exit(1)

            data = response.json()
            
            print("Response has", len(data["items"]), "items")
            
            for question in data["items"]:
                if SKIP_UNANSWERED and question["is_answered"] is None:
                    print(
                        "Warning: the used filter does not contain the 'is_answered' field; accepting all questions",
                        file=sys.stderr,
                    )
                    SKIP_UNANSWERED = False

                if SKIP_UNANSWERED and not question["is_answered"]:
                    continue  # Skip unanswered questions

                question_id = question["question_id"]
                title = question["title"]
                link = question["link"]
                questions.append(
                    {"question_id": question_id, "title": title, "link": link}
                )

            # Info: https://api.stackexchange.com/docs/throttle
            # Respect backoff and avoid making more than 30 requests per second
            backoff = data.get("backoff", 0)
            time.sleep(backoff + (1 / 30))  # Seconds

            # Info: https://api.stackexchange.com/docs/paging
            page = data["page"] + 1
            if not data["has_more"]:
                # Literally ran out of questions to fetch
                print(f"Ran out of questions to fetch, got a total of {len(questions)}")
                break # Try next (site, keyword) pair

            if len(questions) >= TOTAL_QUESTIONS:
                break # Got enough questions

    if len(questions) == 0:
        return # No questions found

    keys = questions[0].keys()
    with open(OUTPUT_FILE_NAME, "w", encoding="utf-8") as output_file:
        dict_writer = csv.DictWriter(output_file, keys)
        dict_writer.writeheader()
        dict_writer.writerows(questions)


def calculate_page_size(total_questions: int, received_so_far: int):
    # Info: https://api.stackexchange.com/docs/paging
    # Each response can contain up to 100 questions

    return min(total_questions - received_so_far, 100)


if __name__ == "__main__":
    main()
