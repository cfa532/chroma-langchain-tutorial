import json, sys, time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Union, List
from fastapi import Depends, FastAPI, HTTPException, status, Request, WebSocket, WebSocketDisconnect
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv, dotenv_values
load_dotenv()

from openaiCBHandler import get_cost_tracker_callback
from leither_api import LeitherAPI
from utilities import ConnectionManager, MAX_TOKEN, UserIn, UserOut, UserInDB
from pet_hash import get_password_hash, verify_password

# to get a string like this run: openssl rand -hex 32
SECRET_KEY = "ebf79dbbdcf6a3c860650661b3ca5dc99b7d44c269316c2bd9fe7c7c5e746274"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480   # expire in 8 hrs
BASE_ROUTE = "/secretari"
connectionManager = ConnectionManager()
lapi = LeitherAPI()

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Union[str, None] = None

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # List of allowed origins
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

def authenticate_user(username: str, password: str):
    user = lapi.get_user(username)
    if user is None:
        return None
    if password != "" and not verify_password(password, user.hashed_password):
        # if password is empty string, this is a temp user. "" not equal to None.
        return None
    return user

def create_access_token(data: dict, expires_delta: Union[timedelta, None] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    user = lapi.get_user(username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

@app.post(BASE_ROUTE+"/token")
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    print("form data", form_data.username, form_data.client_id)
    start_time = time.time()
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    token = Token(access_token=access_token, token_type="Bearer")
    user_out = user.model_dump(exclude=["hashed_password"])
    print("--- %s seconds ---" % (time.time() - start_time))
    return {"token": token, "user": user_out}

@app.post(BASE_ROUTE+"/users/register")
async def register_user(user: UserIn) -> UserOut:
    # If user has tried service, there is valid mid attribute. Otherwise, it is None
    print("User in for register:", user)
    user_in_db = user.model_dump(exclude=["password"])
    user_in_db.update({"hashed_password": get_password_hash(user.password)})  # save hashed password in DB
    user = lapi.register_in_db(UserInDB(**user_in_db))
    if not user:
        raise HTTPException(status_code=400, detail="Username already taken")
    print("User out", user)
    return user

@app.post(BASE_ROUTE+"/users/temp")
async def register_temp_user(user: UserIn):
    # A temp user has assigned username, usuall the device identifier. It does not login, so no taken is needed.
    user_in_db = user.model_dump(exclude=["password"])
    user_in_db.update({"hashed_password": get_password_hash(user.password)})  # save hashed password in DB
    user = lapi.register_temp_user(UserInDB(**user_in_db))
    print("temp user created. ", user)
    if not user:
        raise HTTPException(status_code=400, detail="Failed to create temp User.")
    return user

@app.post(BASE_ROUTE+"/users/redeem")
async def cash_coupon(coupon:str, current_user: Annotated[UserOut, Depends(get_current_user)]) -> bool:
    return lapi.cash_coupon(current_user, coupon)

@app.get(BASE_ROUTE+"/users", response_model=UserOut)
async def get_user_by_id(id: str, current_user: Annotated[UserOut, Depends(get_current_user)]):
    if current_user.role != "admin" and current_user.username != id:
        raise HTTPException(status_code=400, detail="Not admin")
    return lapi.get_user(id)
    # return current_user

@app.get(BASE_ROUTE+"/users/all", response_model=List[UserOut])
async def get_all_users(current_user: Annotated[UserOut, Depends(get_current_user)]):
    if current_user.role != "admin":
        return [UserOut(**current_user.model_dump())] 
    return lapi.get_users()

@app.delete(BASE_ROUTE+"/users/{username}")
async def delete_user_by_id(username: str, current_user: Annotated[UserOut, Depends(get_current_user)]):
    if current_user.role != "admin" and current_user.username != username:
        raise HTTPException(status_code=400, detail="Not admin")
    return lapi.delete_user(username)

#update user infor
@app.put(BASE_ROUTE+"/users")
async def update_user_by_obj(user: UserIn, current_user: Annotated[UserOut, Depends(get_current_user)]):
    if current_user.role != "admin" and current_user.username != user.username:
        raise HTTPException(status_code=400, detail="Not admin")
    user_in_db = user.model_dump(exclude=["password"])

    # if no password, do not update it
    if not user.password:
        user_in_db["hashed_password"] = ""
    else:
        user_in_db["hashed_password"] = get_password_hash(user.password)
    return lapi.update_user(UserInDB(**user_in_db))

@app.get(BASE_ROUTE+"/productids")
async def get_productIDs():
    product_ids = dotenv_values(".env")["SECRETARI_PRODUCT_ID_IOS"]
    # return HTMLResponse("Hello world.")
    return json.loads(product_ids)

@app.get(BASE_ROUTE+"/")
async def get():
    return HTMLResponse("Hello world.")

@app.websocket(BASE_ROUTE+"/ws/")
async def websocket_endpoint(websocket: WebSocket):
    await connectionManager.connect(websocket)
    try:
        while True:
            message = await websocket.receive_text()
            event = json.loads(message)
            print("Incoming event: ", event)
            
            # create the right Chat LLM
            params = event["parameters"]
            if params["llm"] == "openai":
                CHAT_LLM = ChatOpenAI(
                    temperature=float(params["temperature"]),
                    model=params["model"],
                    streaming=True,
                    verbose=True
                    )     # ChatOpenAI cannot have max_token=-1
            elif params["llm"] == "qianfan":
                pass

            # check user account balance. If current model has not balance, use the cheaper default one.
            user = lapi.get_user(event["user"])
            
            llm_model = params["model"]
            if user.dollar_balance[llm_model] <= 0:
                # check default model balance.
                llm_model = "gpt-3.5-turbo"
                if user.dollar_balance[llm_model] <=0:
                    await websocket.send_text(json.dumps({
                        "type": "result",
                        "answer": "Insufficient balance",
                        "tokens": "0",
                        "cost": "0.00",
                        "user": UserOut(**user.model_dump())}))
                    continue

            lapi.bookkeeping(llm_model, 100, 0.01, user)
            await websocket.send_text(json.dumps({
                "type": "result",
                "answer": event["input"]["rawtext"], 
                "tokens": "111",
                "cost": "0.015",
                "user": UserOut(**user.model_dump()).model_dump()}))

            continue
            # CHAT_LLM.callbacks=[MyStreamingHandler()]
            # query = event["input"]["query"]
            # memory = ConversationBufferMemory(return_messages=False)

            query = "The following is a friendly conversation between a human and an AI. The AI is talkative and provides lots of specific details from its context. If the AI does not know the answer to a question, it truthfully says it does not know.\nCurrent conversation:\n"
            if event["input"].get("history"):
                # user server history if history key is not present in user request
                # memory.clear()  # do not use memory on serverside. Add chat history kept by client.
                hlen = 0
                for c in event["input"]["history"]:
                    hlen += len(c["Q"]) + len(c["A"])
                    if hlen > MAX_TOKEN[llm_model]/2:
                        break
                    else:
                        query += "Human: "+c["Q"]+"\nAI: "+c["A"]+"\n"
            query += "Human: "+event["input"]["query"]+"\nAI:"
            print(query)
            start_time = time.time()
            with get_cost_tracker_callback(llm_model) as cb:
                # chain = ConversationChain(llm=CHAT_LLM, memory=memory, output_parser=StrOutputParser())
                chain =CHAT_LLM
                resp = ""
                async for chunk in chain.astream(query):
                    print(chunk.content, end="|", flush=True)    # chunk size can be big
                    resp += chunk.content
                    await websocket.send_text(json.dumps({"type": "stream", "data": chunk.content}))
                print('\n', cb)
                print("time diff=", (time.time() - start_time))
                sys.stdout.flush()
                await websocket.send_text(json.dumps({
                    "type": "result",
                    "answer": resp,
                    "tokens": cb.total_tokens,
                    "cost": cb.total_cost}))
                lapi.bookkeeping(llm_model, cb.total_cost, user)

    except WebSocketDisconnect:
        connectionManager.disconnect(websocket)

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8506)
