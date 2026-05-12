import time, os;
from openai import OpenAI;
t=time.time();
c=OpenAI(api_key=os.getenv('OPENAI_API_KEY'), base_url=os.getenv('OPENAI_BASE_URL') or None);
r=c.chat.completions.create(
    model=os.getenv('OPENAI_MODEL') or 'gpt-4o-mini',
    messages=[{'role':'user','content':'只返回JSON：{\"names\":\"ABS，CAN-H\"}'}],
    temperature=0,
    max_tokens=50,
    timeout=30);
print(time.time()-t);
print(r.choices[0].message.content);
