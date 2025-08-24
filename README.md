A FastAPI-based job scanning service that finds software co-op/internship positions for Fall 2025.
Installation

Install dependencies:

bashpip install fastapi uvicorn requests python-dotenv

Create .env file:

envJSEARCH_API_KEY=your_rapidapi_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here

Run the application:

bashuvicorn api.index:app --reload
