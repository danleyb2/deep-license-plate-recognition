import json
import logging
import os
import sys

import requests

LOG_LEVEL = os.environ.get("LOGGING", "INFO").upper()

logging.basicConfig(
    stream=sys.stdout,
    level=LOG_LEVEL,
    datefmt="%Y-%m-%d %H:%M:%S",
    format="%(levelname)-5s  [%(name)s.%(lineno)d] => %(message)s",
)

lgr = logging.getLogger("code-review-ai")


class GhApi:
    def __init__(self, token):
        self.token = token

    def get_pull_request(self, repo_name, pr_id):
        """
        Fetch PR info from GitHub API
        """
        url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_id}"
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        }
        response = requests.get(url, headers=headers)
        lgr.debug(f"response: {response}")
        lgr.debug("--pr--")
        lgr.debug(response.text)
        lgr.debug("--pr--")
        if response.status_code == 200:
            return response.json()
        else:
            response.raise_for_status()

    def get_pull_request_diff(self, repo_name, pr_id):
        """
        Fetch PR diff from GitHub API
        """
        url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_id}"
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        response = requests.get(url, headers=headers)
        lgr.debug(f"response: {response}")
        if response.status_code == 200:
            return response.text
        else:
            response.raise_for_status()

    def post_review(self, repo_name, pr_id, review_body):
        """
        Post review comment to GitHub PR
        """
        url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_id}/reviews"
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }
        data = {"body": review_body, "event": "COMMENT"}
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 201:
            return response.json()
        else:
            response.raise_for_status()


class AiReviewer:
    intro = """
    You are an expert Senior developer, your task is to review a pull request based on below checklist items:
    1. Assess readability maintainability by making code cleaner
    2. Check for security vulnerabilities such as use of outdated tools or ones with known problems
    3. Consider speed and performance issues such as unnecessary loops and resource-heavy operations
    4. Verify feature requirements based on PR title and description:
        Title: {pr_title}
        Description: {pr_description}
    """

    output = """
    Use markdown formatting for the feedback details ensuring details are accurate with code snippets
    for change suggestions. Format the response in a valid JSON format as a list of feedbacks, where the value is an
    object containing the filename ("fileName") and the feedback  as a ;ist of comments ("comments"). The schema of the
    JSON feedback object must be:

    {
      {
        "fileName":{
            "type": "string"
        },
        "comments": [
            {
                "comment": {
                    "type": "string"
                }
            }
        ]
      }
    }
    The filenames and file contents to review are provided below as output from a `git diff` command on 2 branches:
    """

    def __init__(self, key):
        self.key = key

    def send_to_openai(self, title, description, diff, model="gpt-4-1106-preview"):
        """
        Send prompt to OpenAI API and get the completion
        """
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.key}"}

        system_prompt = (
            AiReviewer.intro.format(pr_title=title, pr_description=description)
            + AiReviewer.output
        )
        data = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": f"Here is the code diff to review:\n\n{diff}",
                },
            ],
            "temperature": 0,
        }
        response = requests.post(url, headers=headers, json=data)
        lgr.debug("-------")
        lgr.debug(response.text)
        lgr.debug("-------")
        if response.status_code == 200:
            return response.json().get("choices")[0].get("message").get("content")
        else:
            response.raise_for_status()


def main():
    github_token = os.environ["GITHUB_TOKEN"]
    openai_api_key = os.environ["OPENAI_API_KEY"]
    repo_name = os.environ["GITHUB_REPOSITORY"]
    pr_id = os.environ["PULL_REQUEST_ID"]

    gh_api = GhApi(github_token)
    pr_info = gh_api.get_pull_request(repo_name, pr_id)
    diff = gh_api.get_pull_request_diff(repo_name, pr_id)
    lgr.debug("--diff--")
    lgr.debug(diff)
    lgr.debug("--diff--")

    ai_reviewer = AiReviewer(openai_api_key)
    ai_review_content: str = ai_reviewer.send_to_openai(
        pr_info["title"], pr_info["body"], diff
    )

    try:
        # ruff: noqa: B005
        feedback_files = json.loads(ai_review_content.rstrip("```").lstrip("```json"))
        lgr.debug(feedback_files)
        pr_comment = ""
        for feedback_file in feedback_files:
            pr_comment += f"**{feedback_file['fileName']}** \n"
            for comment in feedback_file["comments"]:
                pr_comment += f"- [ ] {comment['comment']} \n"
            pr_comment += "\n\n"
    except Exception as e:
        lgr.error("Error parsing AI response:", exc_info=e)
    else:
        gh_api.post_review(repo_name, pr_id, pr_comment)


if __name__ == "__main__":
    main()
