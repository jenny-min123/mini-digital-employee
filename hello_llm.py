from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv()

client = OpenAI(
    api_key=os.getenv("TENCENT_API_KEY"),  # 从 .env 读取，不写死在代码里
    base_url="https://tokenhub.tencentmaas.com/v1"  # TokenHub 统一调用地址
)

resp = client.chat.completions.create(
    model="hy3-preview",  # 可以换成你领取的其他模型
    messages=[{"role": "user", "content": "用一句话说明什么是数字员工"}]
)
print(resp.choices[0].message.content)
