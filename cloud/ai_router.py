import time
from typing import Optional

from google import genai


class AIRouter:
    """
    Reliable Gemini router for JARVIS.

    Tries multiple models and automatically retries
    temporary failures such as 503/429.
    """

    def __init__(
        self,
        api_key: str,
        models: Optional[list[str]] = None,
        max_retries: int = 2,
    ):
        self.api_key = api_key

        self.models = models or [
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
        ]

        self.max_retries = max_retries

        self.client = genai.Client(
            api_key=self.api_key
        )

    def generate(
        self,
        prompt: str,
    ):
        """
        Generate a response using the first
        available Gemini model.
        """

        last_error = None

        for model in self.models:

            print(
                f"[AI Router] Trying model: {model}"
            )

            for attempt in range(
                self.max_retries + 1
            ):

                try:

                    response = (
                        self.client.models.generate_content(
                            model=model,
                            contents=prompt,
                        )
                    )

                    print(
                        f"[AI Router] Success: {model}"
                    )

                    return response

                except Exception as e:

                    last_error = e

                    print(
                        f"[AI Router] "
                        f"{model} failed "
                        f"(attempt "
                        f"{attempt + 1}/"
                        f"{self.max_retries + 1}): "
                        f"{e}"
                    )

                    if attempt < self.max_retries:

                        # Small exponential backoff.
                        wait_time = 2 ** attempt

                        time.sleep(
                            wait_time
                        )

            print(
                f"[AI Router] "
                f"Switching model..."
            )

        raise RuntimeError(
            "All configured AI models failed."
        ) from last_error
