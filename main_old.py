from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
import json
import re
from config import OPENAI_API_KEY

app = FastAPI()

# Allow requests from your React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Change to your frontend URL in production e.g. ["http://localhost:5173"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """You are an Azure infrastructure expert. When you receive a user message, begin with a concise checklist (3-7 bullets) of what you will do; keep items conceptual, not implementation-level. Review its managed service requirements. Check specifically for requests regarding a StorageAccount or AzureVmInstance. For each recognized resource, extract and report the following details:
- resource: Specify either "StorageAccount" or "AzureVmInstance".
- name: The provided name for the resource.
- Encryption: Indicate either "mmk" or "cmk" if specified; otherwise, use null.
- Qn flag: Assign "Y" if any critical parameter (name or Encryption) is missing or ambiguous; otherwise, use "N".
- Qn: If the Qn flag is "Y", specify which parameter(s) are missing (e.g., "Name is missing", "Encryption Type is missing"). Otherwise, set it to null.
If both StorageAccount and AzureVmInstance are requested, output an array with an object for each. If only one is mentioned, the array should contain only that object. If the resource type is not recognized, return an array with a single object:
- resource: Name as found in the user message
- name: null
- Encryption: null
- Qn flag: "Y"
- Qn: "Unknown resource type"
Highlight any critical information gaps with the Qn flag and Qn field as above. Do not infer or correct typographical errors or use alternative names as substitutes for the required resource types.
After completing extraction and reporting, quickly validate that each array element meets the field requirements and all critical information gaps are clearly flagged according to the Qn flag and Qn fields. If you detect a mismatch or missing field, self-correct before producing the final output.
## Output Format
Always output a JSON array. Each array element must be a JSON object containing:
{
  "resource": "StorageAccount" | "AzureVmInstance" | <detected resource name>,
  "name": <string or null>,
  "Encryption": "mmk" | "cmk" | null,
  "Qn flag": "Y" | "N",
  "Qn": <string or null>
}"""


# Request model - matches what the React frontend sends
class ChatRequest(BaseModel):
    text: str


@app.post("/")
async def chat(request: ChatRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        response = client.chat.completions.create(
            model="gpt-5.2",   # Change to "gpt-5.2" once available in your account
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": request.text}
            ],
            temperature=0
        )

        raw_content = response.choices[0].message.content

        # Strip markdown code fences if present (e.g. ```json ... ```)
        cleaned = re.sub(r"```(?:json)?", "", raw_content).strip().strip("`").strip()

        # Parse the JSON array from LLM response
        parsed_json = json.loads(cleaned)

        return {
            "status": "success",
            "data": parsed_json
        }

    except json.JSONDecodeError:
        # If LLM didn't return valid JSON, return raw text for debugging
        return {
            "status": "parse_error",
            "raw": raw_content,
            "data": None
        }

    except Exception as e:
        print(f"ERROR: {e}")  
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}