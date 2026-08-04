# AI-Powered Manufacturing Process Planning Agent

An intelligent manufacturing process planning system that converts natural language part descriptions and engineering PDF drawings into structured, auditable manufacturing process plans using AI.

## 🎯 Overview

This system follows a **Perception → Brain → Action** architecture to generate comprehensive manufacturing process plans while maintaining strict safety and human oversight requirements.

### Key Features

- **Multi-Modal Input Processing**: Supports both text descriptions and engineering PDF drawings
- **AI-Powered Vision Analysis**: Advanced PDF processing with Gemini Vision API
- **Knowledge-Grounded Generation**: Uses approved reference cases for evidence-based planning
- **Structured Output**: Generates validated JSON process plans with detailed operations
- **Safety-First Design**: Never generates executable G-code, requires human approval
- **Conflict Detection**: Identifies and resolves inconsistencies in engineering drawings

## 🏗️ Architecture

```
Input Layer (Text/PDF) → Processing Pipeline → AI Reasoning Engine → Validated Output
                            ↓
                    Knowledge Base Search ← Reference Cases (9 approved examples)
```

### Core Components

- **Perception Layer**: Input validation, PDF processing, vision analysis
- **Manufacturing Brain**: Knowledge retrieval, prompt engineering, LLM generation  
- **Action Layer**: Rule enforcement, validation, output formatting

## 🚀 Quick Start

### Prerequisites

- Python 3.13+
- Google Gemini API key
- Windows (for SSL certificate handling)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/Dobby-velankani/AI-powered-Manufacturing-Process-Planning-Agent.git
cd AI-powered-Manufacturing-Process-Planning-Agent
```

2. Create virtual environment:
```bash
python -m venv .venv
.\.venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create `.env` file:
```bash
# Copy the example file
cp .env.example .env

# Edit .env file with your API key
# Get Gemini API key from: https://makersuite.google.com/app/apikey
```

Example `.env` content:
```bash
GEMINI_API_KEY=your_actual_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash
GEMINI_FALLBACK_MODELS=gemini-1.5-pro,gemini-1.5-flash
```

⚠️ **Security Notice**: Never commit your `.env` file to version control!

5. Run the application:
```bash
python main.py
```

## 📖 Usage

### Text Input Mode
```
Select input type: 1
Describe the new part: I need to make a steel shaft, 20mm diameter, 100mm long, with a keyway
```

### PDF Input Mode  
```
Select input type: 2
Enter the PDF path: path/to/engineering_drawing.pdf
```

The system will:
1. Process the input (text parsing or PDF vision analysis)
2. Search reference cases for similar manufacturing examples
3. Generate a structured process plan using Gemini AI
4. Validate and enforce safety rules
5. Save results as timestamped JSON files

## 📁 Project Structure

```
├── main.py                 # CLI entry point
├── app/
│   ├── agents/             # Process planning orchestration
│   ├── document_processing/ # PDF processing pipeline
│   ├── llm/               # AI provider implementations
│   ├── models/            # Data models (Pydantic schemas)
│   ├── services/          # Knowledge base management
│   └── validation/        # Process plan validation
├── database/              # Reference manufacturing cases
└── outputs/              # Generated process plans
```

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Gemini API key (required) | - |
| `GEMINI_MODEL` | Primary Gemini model | `gemini-3.5-flash` |
| `GEMINI_FALLBACK_MODELS` | Fallback models (comma-separated) | - |
| `GEMINI_CA_BUNDLE` | Custom SSL certificate bundle | - |

### Manufacturing Philosophy

The system embeds manufacturing best practices:
- Prefer manual machining where practical
- Use CNC only for precision, complexity, or repeatability  
- Consider datums, workholding, and distortion
- Never generate executable G-code
- Require human approval for all plans

## 🛡️ Safety Features

- **No CNC Code Generation**: Hardcoded prevention of G/M-code output
- **Mandatory Human Approval**: All plans require engineering review
- **Conflict Detection**: Identifies drawing inconsistencies
- **SSL Security**: Proper certificate handling for API calls
- **Consent Management**: Explicit user approval for cloud uploads

## 📊 Output Format

Generated process plans include:
- Part interpretation and material recommendations
- Complete operation sequence with setup details
- Inspection plans and tooling requirements
- Risk assessments and missing information
- Reference case traceability

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🏭 Manufacturing Knowledge Base

The system includes 9 approved reference manufacturing cases covering:
- Various materials (C45, S235, EN31, etc.)
- Different part types (shafts, plates, gears, washers)
- Multiple manufacturing processes (turning, milling, grinding)
- Industry best practices and lessons learned
