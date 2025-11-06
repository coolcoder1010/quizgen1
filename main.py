import os
import json
from flask import Flask, request, jsonify, render_template_string
from google import genai
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

app = Flask(__name__)

# --- Gemini API Prompt ---
# The detailed prompt instructs Gemini to return a structured JSON object
GEMINI_QUIZ_PROMPT = """
You are an expert educational assessment creator. Your task is to generate a comprehensive quiz based on the provided text.

**INSTRUCTIONS:**
1. **Source Text:** Use ONLY the following text as the source material:
   ---
   {source_text}
   ---
2. **Question Count:** Generate exactly {num_questions} questions.
3. **Question Types:** Ensure a diverse mix of the following types: Multiple Choice (MC), True/False (TF), and Short Answer (SA).
4. **Output Format:** You MUST return the output as a single, valid JSON object with two top-level keys: "quiz" and "answers". Do not include any text outside of the JSON object.

**JSON STRUCTURE EXAMPLE:**
{{
    "quiz": [
        {{
            "id": 1,
            "type": "MC",
            "question": "What is the primary function of the mitochondria?",
            "options": ["To produce ATP", "To digest waste", "To hold the DNA"] 
        }},
        // ... more questions
    ],
    "answers": [
        {{
            "id": 1,
            "correct_answer": "To produce ATP"
        }},
        // ... corresponding answers
    ]
}}

**START GENERATION NOW.**
"""

# --- Service Initialization ---

# Initialize Gemini Client (reads GEMINI_API_KEY from environment)
try:
    gemini_client = genai.Client()
except Exception as e:
    print(f"Gemini client initialization failed: {e}")
    gemini_client = None

def get_google_docs_service():
    """Builds and returns the Google Docs service client, authenticated via
    Application Default Credentials (ADC) which automatically uses the 
    Cloud Run service identity."""
    
    # Cloud Run uses its default service account for ADC
    # The required scope is for Google Drive/Docs access
    SCOPES = ['https://www.googleapis.com/auth/documents', 
              'https://www.googleapis.com/auth/drive.file']
    
    # Get credentials for the service account assigned to Cloud Run
    # google.auth.default() will find the credentials automatically
    credentials, project = google_requests.default(scopes=SCOPES)
    
    # Build the Docs service
    service = build('docs', 'v1', credentials=credentials)
    return service

def create_google_doc(service, title, content):
    """Creates a new Google Doc and inserts content."""
    
    # 1. Create a blank document
    document = service.documents().create(body={'title': title}).execute()
    doc_id = document.get('documentId')

    # 2. Prepare the insertion request (batchUpdate)
    requests = []
    
    # Insert the content
    requests.append({
        'insertText': {
            'location': {'index': 1}, 
            'text': content
        }
    })
    
    # 3. Execute the update
    service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
    
    return f"https://docs.google.com/document/d/{doc_id}/edit"


@app.route('/', methods=['GET'])
def index():
    """Simple HTML frontend for the user input."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>AI Quiz Generator</title>
        <style>
            body { font-family: sans-serif; max-width: 800px; margin: auto; padding: 20px; }
            textarea { width: 100%; height: 300px; padding: 10px; margin-bottom: 10px; }
            input[type="number"] { width: 80px; padding: 5px; }
            button { padding: 10px 20px; cursor: pointer; background-color: #4285F4; color: white; border: none; border-radius: 5px; }
            #status { margin-top: 20px; font-weight: bold; }
        </style>
    </head>
    <body>
        <h2>Google Cloud Hackathon Quiz Generator</h2>
        <p>Paste your notes, textbook material, or type any content below. The AI will generate a quiz based on this source text.</p>
        
        <textarea id="sourceText" placeholder="Paste your educational content here... (e.g., 'The capital of France is Paris. The Eiffel Tower was completed in 1889.')"></textarea><br>
        
        <label for="numQuestions">Number of Questions (Max 20):</label>
        <input type="number" id="numQuestions" value="10" min="1" max="20"><br><br>
        
        <button onclick="generateQuiz()">Generate & Export to Google Docs</button>
        
        <div id="status"></div>

        <script>
            async function generateQuiz() {
                const sourceText = document.getElementById('sourceText').value;
                const numQuestions = document.getElementById('numQuestions').value;
                const statusDiv = document.getElementById('status');
                
                if (sourceText.length < 50) {
                    statusDiv.innerHTML = 'Please enter more source text.';
                    return;
                }
                
                statusDiv.innerHTML = 'Generating quiz using Gemini AI...';
                
                try {
                    const response = await fetch('/generate', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ source_text: sourceText, num_questions: numQuestions })
                    });
                    
                    const data = await response.json();
                    
                    if (response.ok) {
                        statusDiv.innerHTML = `✅ **Success!** Quiz exported to Google Docs: <a href="${data.doc_url}" target="_blank">${data.doc_url}</a>`;
                    } else {
                        statusDiv.innerHTML = `❌ Error: ${data.error || 'Unknown error occurred.'}`;
                    }
                    
                } catch (error) {
                    statusDiv.innerHTML = `❌ Network Error: ${error.message}`;
                }
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html_content)


@app.route('/generate', methods=['POST'])
def generate():
    """Handles the quiz generation and Google Docs export."""
    if gemini_client is None:
        return jsonify({"error": "Gemini Client not initialized. Check API Key configuration."}), 500

    try:
        data = request.get_json()
        source_text = data.get('source_text', '')
        num_questions = int(data.get('num_questions', 10))
    except Exception:
        return jsonify({"error": "Invalid input data."}), 400

    # 1. Format the detailed prompt
    prompt = GEMINI_QUIZ_PROMPT.format(
        source_text=source_text, 
        num_questions=min(num_questions, 20) # Cap at 20 questions
    )
    
    try:
        # 2. Call the Gemini API
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        
        # 3. Parse the JSON output from the model
        # Use simple string parsing to find the JSON block if the model adds fluff
        json_start = response.text.find('{')
        json_end = response.text.rfind('}') + 1
        json_string = response.text[json_start:json_end]
        
        quiz_data = json.loads(json_string)
        
    except Exception as e:
        print(f"Gemini or JSON Parsing Error: {e}\nRaw Response: {response.text[:500] if response else 'N/A'}")
        return jsonify({"error": "Failed to generate structured quiz from AI. Try adjusting the source text."}), 500

    # 4. Format Content for Google Docs
    doc_content = "Generated Quiz\n\n---\n"
    
    # Add Questions
    doc_content += "--- QUESTIONS ---\n"
    for item in quiz_data.get('quiz', []):
        q_text = f"{item['id']}. ({item['type']}) {item['question']}"
        if item['type'] == 'MC' and 'options' in item:
            options_text = "\n".join([f"    - {opt}" for opt in item['options']])
            q_text += f"\n{options_text}"
        doc_content += f"{q_text}\n\n"
        
    # Add Answers
    doc_content += "\n\n--- ANSWER KEY ---\n"
    for item in quiz_data.get('answers', []):
        doc_content += f"{item['id']}. {item['correct_answer']}\n"

    # 5. Export to Google Docs
    try:
        docs_service = get_google_docs_service()
        doc_url = create_google_doc(docs_service, 
                                    f"AI Quiz - {min(num_questions, 20)} Questions", 
                                    doc_content)
        
        return jsonify({"success": True, "doc_url": doc_url})

    except HttpError as err:
        error_message = f"Google Docs API Error: {err.content.decode()}"
        print(error_message)
        return jsonify({"error": f"Failed to create Google Doc. Check Cloud Run permissions. Error: {err.resp.status}"}), 500
    except Exception as e:
        print(f"General Docs Export Error: {e}")
        return jsonify({"error": "An unexpected error occurred during Docs export."}), 500


if __name__ == '__main__':
    # Running locally for testing
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))