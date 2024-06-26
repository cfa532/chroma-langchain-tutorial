import re, sys
from typing import Any
from flask import Flask, render_template, request, make_response
from flask_socketio import SocketIO, emit, send
from flask_cors import CORS

from langchain_openai import ChatOpenAI
from langchain_community.vectorstores import Chroma
from langchain.memory import ConversationBufferWindowMemory
from langchain.chains import ConversationChain
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_core.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.globals import set_debug

from init_vectordb import upsert_text, extract_text
from case_handler import init_case, query_docstore, get_request, get_argument, get_basic_info
from config import EMBEDDING_FUNC, LegalCase, llm_chain, LAW_COLLECTION_NAME, MAX_TOKENS, LLM
CHROMA_CLIENT = None
# from openai import OpenAI
# client = OpenAI()
# model = "gpt-4"
HALF_TOKENS = MAX_TOKENS/2

# os.environ["TOKENIZERS_PARALLELISM"] = "false"
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.debug = False

app.config['SECRET_KEY'] = "secret!"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*",  max_http_buffer_size=app.config['MAX_CONTENT_LENGTH'])

class MyStreamingHandler(StreamingStdOutCallbackHandler):
    def __init__(self) -> None:
        super().__init__()

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        # super().__init__()
        sys.stdout.write(token)
        emit("stream_in", token)    # reply to event sent from the client, socketio.emit reply to all clients
        sys.stdout.flush()

CHAT_LLM = ChatOpenAI(temperature=0, model="gpt-4", max_tokens=2048, streaming=True,
                      callbacks=[MyStreamingHandler()])     # ChatOpenAI cannot have max_token=-1
chain = ConversationChain(llm=CHAT_LLM, memory=ConversationBufferWindowMemory(k=6), verbose=False)
chain.output_parser = StrOutputParser()

@socketio.on("gpt_api")
def gpt_api(chat_history, query:str):
    # resp = chain.predict(input=query)
    # return resp
    for chunk in chain.stream(query):
        print(chunk, end="", flush=True)    # chunk size can be big
    return

    # # build a query with chat history and send it to GPT
    # # most recent chat message comes in first. Limit chat history to 2000 tokens
    # prev_chat = ""
    # for chat in chat_history:
    #     ch = "User says " + chat["Q"] + ". AI says " + chat["A"] +"."
    #     print(ch)
    # #     if len(prev_chat)+len(ch) >= HALF_TOKENS:
    # #         prev_chat += ch[:(HALF_TOKENS-len(prev_chat))]
    # #         break
    # #     else:
    # #         prev_chat += ch
    # # print("聊天历史记录：", prev_chat)
    # stream = client.chat.completions.create(
    #     model=model,
    #     stream=True,
    #     messages=[
    #         {"role": "system", "content": "You are a helpful Chinese assistant designed to answer questions in Chinese."},
    #         {"role": "user", "content": query},
    #         # {"role": "assistant", "content": prev_chat}
    #     ]
    # )
    # answer = ""
    # for chunk in stream:
    #     if chunk.choices[0].delta.content is not None:
    #         answer += chunk.choices[0].delta.content
    #         print(chunk.choices[0].delta.content, end="")
    #         socketio.emit("stream_in", answer)
    # return answer

# query case documents to figure basic informations about involved parties.
# Always return the result and refined query
@socketio.on("case_info")
def case_info(my_case:LegalCase, query:str):
    print(my_case, query)
    # query += "原告是"+my_case["plaintiff"]+", 被告是"+my_case["defendant"]+"。 "
    db = Chroma(client=CHROMA_CLIENT, collection_name=my_case["mid"], embedding_function=EMBEDDING_FUNC)
    db_retriever = db.as_retriever(search_kwargs={"filter":{"doc_type":my_case["id"]}})
    # query = "根据所提供资料，分别确定原告方及被告的基本信息。如当事人是公民（自然人），应写明姓名、性别、民族、出生年月日、住址、身份证号码、联系方式；当事人如是机关、团体、企事业单位，则写明名称、地址、统一社会信用代码、法定代表人姓名、职务"
    return get_basic_info(db_retriever, query)

@socketio.on("case_request")
def case_request(my_case:LegalCase, query:str):
    print(my_case, query)
    db = Chroma(client=CHROMA_CLIENT, collection_name=my_case["mid"], embedding_function=EMBEDDING_FUNC)
    db_retriever = db.as_retriever(search_kwargs={"filter":{"doc_type":my_case["id"]}})
    query += "原告是"+my_case["plaintiff"]+", 被告是"+my_case["defendant"]+"。 "

    # 可以在此调整温度参数
    res, query = get_request(db_retriever, query, 0.7)
    print("Result: ", res, query)
    return res

@socketio.on("case_wrongs")
def case_wrongs(my_case:LegalCase, query:str):
    print(my_case, query, '\n')
    laws_retriever = Chroma(client=CHROMA_CLIENT, collection_name=LAW_COLLECTION_NAME, embedding_function=EMBEDDING_FUNC).as_retriever()
    docs_db = Chroma(client=CHROMA_CLIENT, collection_name=my_case["mid"], embedding_function=EMBEDDING_FUNC)
    db_retriever = docs_db.as_retriever(search_kwargs={"filter":{"doc_type":my_case["id"]}})
    # wrongdoings of the defendant, seperate it into a list.
    # LLM will return a string, extract the array from it.
    task_list = re.findall('".+"', llm_chain("Seperate the following text into a list of wrong doings by the defendant, export it as an array. Use the original text directly." + query))
    print(task_list)

    for t in task_list[:1]:
        print("Task: ", t)
        socketio.emit("process_task", t)   # tell client current task being processed
        # process each wrong doings
        facts = query_docstore(db_retriever, "从所提供资料中，查询与下述声明相关的事实。"+t)
        print("FACTS: ", facts)

        # figure out the laws violated
        laws = llm_chain("下述声明会涉及到哪几部相关法律？"+t)
        print("Laws: " + laws)
        for l in re.findall('《.+》', laws)[:1]:
            print("LAW: ", l)
            law_items=query_docstore(laws_retriever, t+" 触及 "+l+" 的那些具体条款？在回答中引用具体条款内容。")
            print("具体条款: ", law_items)
            res=llm_chain("You are "+my_case["role"]+". Use the information provided to make an argument about the case. " + facts["result"] + ". Concerning the following law, " + l + " and the following items. " + law_items["result"])
            print("陈述: ", res)
            socketio.emit("task_result", {"argument": res, "law": law_items["result"]})

    print("case done")
    return laws     # list of relevant laws

@socketio.on("case_argument")
def case_argument(collection_name:str, query:str):
    res, query = get_argument(collection_name, query)
    
    
    ("Argument: ", res, query)
    return res, query

# Upload a file from web client
@socketio.on("upload_file")
def upload(collection_name, case_name, filename, filetype, filedata):
    print("Received file: ", collection_name, case_name, filename, len(filedata))
    text = extract_text(filename, filetype, filedata)
    print(text[:100])
    res =  upsert_text(collection_name, text, filename, case_name)
    print(res)
    # emit("file_uploaded", filename)
    return res

@app.route('/')
def hello_world():
    return 'Hello'

if __name__=='__main__':
    # from waitress import serve
    # serve(app, host="0.0.0.0", port=5000)
    socketio.run(app, host='0.0.0.0', port=5050)



"""
@app.route('/init', methods=["GET", "POST"])
def init():
    # assume there is only one file
    file = request.files.getlist('file')[0]
    # get text content of the file
    # text = extract_text(file)
    text = init_case(text)
    resp = make_response(text)
    resp.headers["Access-Control-Allow-Origin"] = '*'       # In request header, use {Mode: cors}
    # print_object(resp)
    return resp
"""


@socketio.on("hello")
def sayHi(arg):
    print(arg); # "world"
    return {"status": "greata"}     # returned parameter to the callback defined in client

# given a file to extract basic information of a case, such as plaintiff and defendent
@socketio.on("init_case")
def init(filename, filetype, filedata):
    print("Init case:", filename, filetype)
    text = extract_text(filename, filetype, filedata)
    res = init_case(text)
    print(res)   # AI result and refined query
    # res = {"title": "田产地头纠纷", "brief":"张三告李四多吃多占", "plaintiff":"张三", "defendant":"李四"}
    return res
