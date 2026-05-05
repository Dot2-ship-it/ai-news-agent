from __future__ import annotations

import os

from openai import OpenAI


class OpenAITranslator:
    def __init__(self, model: str) -> None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("Missing DEEPSEEK_API_KEY")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self.model = model

    def translate_key_parts(self, title: str, content: str) -> str:
        prompt = f"""
请把下面英文 AI/科技文章转成中文编辑材料。不要逐字全文翻译，不要扩写，不要编造。
只输出：中文标题、核心观点、关键事实和关键段落的中文转述。

标题：{title}

正文：
{content[:10000]}
"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""
