import requests
from datetime import datetime
from bilibiliuploader.util import cipher as cipher
from urllib import parse
import os
import math
import hashlib
from bilibiliuploader.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
import base64

# From PC ugc_assisstant
APPKEY = 'aae92bc66f3edfab'
APPSECRET = 'af125a0d5279fd576c1b4418a3e8276d'

# upload chunk size = 2MB
CHUNK_SIZE = 2 * 1024 * 1024

# captcha
CAPTCHA_RECOGNIZE_URL = "http://66.112.209.22:8889/captcha"

class VideoPart:
    """
    Video Part of a post.
    每个对象代表一个分P

    Attributes:
        path: file path in local file system.
        title: title of the video part.
        desc: description of the video part.
        server_file_name: file name in bilibili server. generated by pre-upload API.
    """
    def __init__(self, path, title='', desc='', server_file_name=None):
        self.path = path
        self.title = title
        self.desc = desc
        self.server_file_name=server_file_name

    def __repr__(self):
        return '<{clazz}, path: {path}, title: {title}, desc: {desc}, server_file_name:{server_file_name}>'\
            .format(clazz=self.__class__.__name__,
                    path=self.path,
                    title=self.title,
                    desc=self.desc,
                    server_file_name=self.server_file_name)


def get_key(sid=None, jsessionid=None):
    """
    get public key, hash and session id for login.
    Args:
        sid: session id. only for captcha login.
        jsessionid: j-session id. only for captcha login.
    Returns:
        hash: salt for password encryption.
        pubkey: rsa public key for password encryption.
        sid: session id.
    """
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': "application/json, text/javascript, */*; q=0.01"
    }
    post_data = {
        'appkey': APPKEY,
        'platform': "pc",
        'ts': str(int(datetime.now().timestamp()))
    }
    post_data['sign'] = cipher.sign_dict(post_data, APPSECRET)
    cookie = {}
    if sid:
        cookie['sid'] = sid
    if jsessionid:
        cookie['JSESSIONID'] = jsessionid
    r = requests.post(
        "https://passport.bilibili.com/api/oauth2/getKey",
        headers=headers,
        data=post_data,
        cookies=cookie
    )
    r_data = r.json()['data']
    if sid:
        return r_data['hash'], r_data['key'], sid
    return r_data['hash'], r_data['key'], r.cookies['sid']


def get_capcha(sid):
    headers = {
        'User-Agent': '',
        'Accept-Encoding': 'gzip,deflate',
    }

    params = {
        'appkey': APPKEY,
        'platform': 'pc',
        'ts': str(int(datetime.now().timestamp()))
    }
    params['sign'] = cipher.sign_dict(params, APPSECRET)

    r = requests.get(
        "https://passport.bilibili.com/captcha",
        headers=headers,
        params=params,
        cookies={
            'sid': sid
        }
    )

    print(r.status_code)

    capcha_data = r.content

    return r.cookies['JSESSIONID'], capcha_data


def recognize_captcha(img: bytes):
    img_base64 = str(base64.b64encode(img), encoding='utf-8')
    r = requests.post(
        url=CAPTCHA_RECOGNIZE_URL,
        data={'image': img_base64}
    )
    return r.content.decode()


def login(username, password):
    """
    bilibili login.
    Args:
        username: plain text username for bilibili.
        password: plain text password for bilibili.

    Returns:
        code: login response code (0: success, -105: captcha error, ...).
        access_token: token for further operation.
        refresh_token: token for refresh access_token.
        sid: session id.
        mid: member id.
        expires_in: access token expire time (30 days)
    """
    hash, pubkey, sid = get_key()

    encrypted_password = cipher.encrypt_login_password(password, hash, pubkey)
    url_encoded_username = parse.quote_plus(username)
    url_encoded_password = parse.quote_plus(encrypted_password)

    post_data = {
        'appkey': APPKEY,
        'password': url_encoded_password,
        'platform': "pc",
        'ts': str(int(datetime.now().timestamp())),
        'username': url_encoded_username
    }

    post_data['sign'] = cipher.sign_dict(post_data, APPSECRET)
    # avoid multiple url parse
    post_data['username'] = username
    post_data['password'] = encrypted_password

    headers = {
        'Connection': 'keep-alive',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'User-Agent': '',
        'Accept-Encoding': 'gzip,deflate',
    }

    r = requests.post(
        "https://passport.bilibili.com/api/v3/oauth2/login",
        headers=headers,
        data=post_data,
        cookies={
            'sid': sid
        }
    )
    response = r.json()
    response_code = response['code']
    if response_code == 0:
        login_data = response['data']['token_info']
        return response_code, login_data['access_token'], login_data['refresh_token'], sid, login_data['mid'], login_data["expires_in"]
    elif response_code == -105: # captcha error, retry=5
        retry_cnt = 5
        while response_code == -105 and retry_cnt > 0:
            response_code, access_token, refresh_token, sid, mid, expire_in = login_captcha(username, password, sid)
            if response_code == 0:
                return response_code, access_token, refresh_token, sid, mid, expire_in
            retry_cnt -= 1

    # other error code
    return response_code, None, None, sid, None, None


def login_captcha(username, password, sid):
    """
    bilibili login with captcha.
    depend on captcha recognize service, please do not use this as first choice.
    Args:
        username: plain text username for bilibili.
        password: plain text password for bilibili.
        sid: session id
    Returns:
        code: login response code (0: success, -105: captcha error, ...).
        access_token: token for further operation.
        refresh_token: token for refresh access_token.
        sid: session id.
        mid: member id.
        expires_in: access token expire time (30 days)
    """

    jsessionid, captcha_img = get_capcha(sid)
    captcha_str = recognize_captcha(captcha_img)

    hash, pubkey, sid = get_key(sid, jsessionid)

    encrypted_password = cipher.encrypt_login_password(password, hash, pubkey)
    url_encoded_username = parse.quote_plus(username)
    url_encoded_password = parse.quote_plus(encrypted_password)

    post_data = {
        'appkey': APPKEY,
        'captcha': captcha_str,
        'password': url_encoded_password,
        'platform': "pc",
        'ts': str(int(datetime.now().timestamp())),
        'username': url_encoded_username
    }

    post_data['sign'] = cipher.sign_dict(post_data, APPSECRET)
    # avoid multiple url parse
    post_data['username'] = username
    post_data['password'] = encrypted_password
    post_data['captcha'] = captcha_str

    headers = {
        'Connection': 'keep-alive',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'User-Agent': '',
        'Accept-Encoding': 'gzip,deflate',
    }

    r = requests.post(
        "https://passport.bilibili.com/api/oauth2/login",
        headers=headers,
        data=post_data,
        cookies={
            'JSESSIONID': jsessionid,
            'sid': sid
        }
    )
    response = r.json()
    if response['code'] == 0:
        login_data = response['data']
        return response['code'], login_data['access_token'], login_data['refresh_token'], sid, login_data['mid'], login_data["expires_in"]
    else:
        return response['code'], None, None, sid, None, None


def login_by_access_token(access_token):
    """
    bilibili access token login.
    Args:
        access_token: Bilibili access token got by previous username/password login.

    Returns:
        sid: session id.
        mid: member id.
        expires_in: access token expire time
    """
    headers = {
        'Connection': 'keep-alive',
        'Accept-Encoding': 'gzip,deflate',
        'Host': 'passport.bilibili.com',
        'User-Agent': '',
    }

    login_params = {
        'appkey': APPKEY,
        'access_token': access_token,
        'platform': "pc",
        'ts': str(int(datetime.now().timestamp())),
    }
    login_params['sign'] = cipher.sign_dict(login_params, APPSECRET)

    r = requests.get(
        url="https://passport.bilibili.com/api/oauth2/info",
        headers=headers,
        params=login_params
    )

    login_data = r.json()['data']

    return r.cookies['sid'], login_data['mid'], login_data["expires_in"]


def upload_cover(access_token, sid, cover_file_path):
    with open(cover_file_path, "rb") as f:
        cover_pic = f.read()

    headers = {
        'Connection': 'keep-alive',
        'Host': 'member.bilibili.com',
        'Accept-Encoding': 'gzip,deflate',
        'User-Agent': '',
    }

    params = {
        "access_key": access_token,
    }

    params["sign"] = cipher.sign_dict(params, APPSECRET)

    files = {
        'file': ("cover.png", cover_pic, "Content-Type: image/png"),
    }

    r = requests.post(
        "http://member.bilibili.com/x/vu/client/cover/up",
        headers=headers,
        params=params,
        files=files,
        cookies={
            'sid': sid
        },
        verify=False,
    )

    return r.json()["data"]["url"]


def upload_chunk(upload_url, server_file_name, local_file_name, chunk_data, chunk_size, chunk_id, chunk_total_num):
    """
    upload video chunk.
    Args:
        upload_url: upload url by pre_upload api.
        server_file_name: file name on server by pre_upload api.
        local_file_name: video file name in local fs.
        chunk_data: binary data of video chunk.
        chunk_size: default of ugc_assisstant is 2M.
        chunk_id: chunk number.
        chunk_total_num: total chunk number.

    Returns:
        True: upload chunk success.
        False: upload chunk fail.
    """
    print("chunk{}/{}".format(chunk_id, chunk_total_num))
    print("filename: {}".format(local_file_name))
    files = {
        'version': (None, '2.0.0.1054'),
        'filesize': (None, chunk_size),
        'chunk': (None, chunk_id),
        'chunks': (None, chunk_total_num),
        'md5': (None, cipher.md5_bytes(chunk_data)),
        'file': (local_file_name, chunk_data, 'application/octet-stream')
    }

    r = requests.post(
        url=upload_url,
        files=files,
        cookies={
            'PHPSESSID': server_file_name
        },
    )
    print(r.status_code)
    print(r.content)

    if r.status_code == 200 and r.json()['OK'] == 1:
        return True
    else:
        return False


def upload_video_part(access_token, sid, mid, video_part: VideoPart, max_retry=5):
    """
    upload a video file.
    Args:
        access_token: access token generated by login api.
        sid: session id.
        mid: member id.
        video_part: local video file data.
        max_retry: max retry number for each chunk.

    Returns:
        status: success or fail.
        server_file_name: server file name by pre_upload api.
    """
    headers = {
        'Connection': 'keep-alive',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'User-Agent': '',
        'Accept-Encoding': 'gzip,deflate',
    }

    r = requests.get(
        "http://member.bilibili.com/preupload?access_key={}&mid={}&profile=ugcfr%2Fpc3".format(access_token, mid),
        headers=headers,
        cookies={
            'sid': sid
        },
        verify=False,
    )

    pre_upload_data = r.json()
    upload_url = pre_upload_data['url']
    complete_upload_url = pre_upload_data['complete']
    server_file_name = pre_upload_data['filename']
    local_file_name = video_part.path

    file_size = os.path.getsize(local_file_name)
    chunk_total_num = int(math.ceil(file_size / CHUNK_SIZE))
    file_hash = hashlib.md5()
    with open(local_file_name, 'rb') as f:
        for chunk_id in range(0, chunk_total_num):
            chunk_data = f.read(CHUNK_SIZE)

            status = Retry(max_retry=max_retry, success_return_value=True).run(
                upload_chunk,
                upload_url,
                server_file_name,
                os.path.basename(local_file_name),
                chunk_data,
                CHUNK_SIZE,
                chunk_id,
                chunk_total_num
            )

            if not status:
                return False
            file_hash.update(chunk_data)
    print(file_hash.hexdigest())

    # complete upload
    post_data = {
        'chunks': chunk_total_num,
        'filesize': file_size,
        'md5': file_hash.hexdigest(),
        'name': os.path.basename(local_file_name),
        'version': '2.0.0.1054',
    }

    r = requests.post(
        url=complete_upload_url,
        data=post_data,
        headers=headers,
    )
    print(r.status_code)
    print(r.content)

    video_part.server_file_name = server_file_name

    return True


def upload(access_token,
           sid,
           mid,
           parts,
           copyright: int,
           title: str,
           tid: int,
           tag: str,
           desc: str,
           source: str = '',
           cover: str = '',
           no_reprint: int = 0,
           open_elec: int = 1,
           max_retry: int = 5,
           thread_pool_workers: int = 1):
    """
    upload video.

    Args:
        access_token: oauth2 access token.
        sid: session id.
        mid: member id.
        parts: VideoPart list.
        copyright: 原创/转载.
        title: 投稿标题.
        tid: 分区id.
        tag: 标签.
        desc: 投稿简介.
        source: 转载地址.
        cover: 封面图片文件路径.
        no_reprint: 可否转载.
        open_elec: 充电.
        max_retry: max retry time for each chunk.
        thread_pool_workers: max upload threads.

    Returns:
        (aid, bvid)
        aid: av号
        bvid: bv号
    """
    if not isinstance(parts, list):
        parts = [parts]

    status = True
    with ThreadPoolExecutor(max_workers=thread_pool_workers) as tpe:
        t_list = []
        for video_part in parts:
            print("upload {} added in pool".format(video_part.title))
            t_obj = tpe.submit(upload_video_part, access_token, sid, mid, video_part, max_retry)
            t_obj.video_part = video_part
            t_list.append(t_obj)

        for t_obj in as_completed(t_list):
            status = status and t_obj.result()
            print("video part {} finished, status: {}".format(t_obj.video_part.title, t_obj.result()))
            if not status:
                print("upload failed")
                return None, None

    # cover
    if os.path.isfile(cover):
        try:
            cover = upload_cover(access_token, sid, cover)
        except:
            cover = ''
    else:
        cover = ''

    # submit
    headers = {
        'Connection': 'keep-alive',
        'Content-Type': 'application/json',
        'User-Agent': '',
    }
    post_data = {
        'build': 1054,
        'copyright': copyright,
        'cover': cover,
        'desc': desc,
        'no_reprint': no_reprint,
        'open_elec': open_elec,
        'source': source,
        'tag': tag,
        'tid': tid,
        'title': title,
        'videos': []
    }
    for video_part in parts:
        post_data['videos'].append({
            "desc": video_part.desc,
            "filename": video_part.server_file_name,
            "title": video_part.title
        })

    params = {
        'access_key': access_token,
    }
    params['sign'] = cipher.sign_dict(params, APPSECRET)
    r = requests.post(
        url="http://member.bilibili.com/x/vu/client/add",
        params=params,
        headers=headers,
        verify=False,
        cookies={
            'sid': sid
        },
        json=post_data,
    )

    print("submit")
    print(r.status_code)
    print(r.content.decode())

    data = r.json()["data"]
    return data["aid"], data["bvid"]


def get_post_data(access_token, sid, avid):
    headers = {
        'Connection': 'keep-alive',
        'Host': 'member.bilibili.com',
        'Accept-Encoding': 'gzip,deflate',
        'User-Agent': '',
    }

    params = {
        "access_key": access_token,
        "aid": avid,
        "build": "1054"
    }

    params["sign"] = cipher.sign_dict(params, APPSECRET)

    r = requests.get(
        url="http://member.bilibili.com/x/client/archive/view",
        headers=headers,
        params=params,
        cookies={
            'sid': sid
        }
    )

    return r.json()["data"]


def edit_videos(
        access_token,
        sid,
        mid,
        avid=None,
        bvid=None,
        parts=None,
        insert_index=None,
        copyright=None,
        title=None,
        tid=None,
        tag=None,
        desc=None,
        source=None,
        cover=None,
        no_reprint=None,
        open_elec=None,
        max_retry: int = 5,
        thread_pool_workers: int = 1):
    """
    insert videos into existed post.

    Args:
        access_token: oauth2 access token.
        sid: session id.
        mid: member id.
        avid: av number,
        bvid: bv string,
        parts: VideoPart list.
        insert_index: new video index.
        copyright: 原创/转载.
        title: 投稿标题.
        tid: 分区id.
        tag: 标签.
        desc: 投稿简介.
        source: 转载地址.
        cover: cover url.
        no_reprint: 可否转载.
        open_elec: 充电.
        max_retry: max retry time for each chunk.
        thread_pool_workers: max upload threads.

    Returns:
        (aid, bvid)
        aid: av号
        bvid: bv号
    """
    if not avid and not bvid:
        print("please provide avid or bvid")
        return None, None
    if not avid:
        avid = cipher.bv2av(bvid)
    if not isinstance(parts, list):
        parts = [parts]
    if type(avid) is str:
        avid = int(avid)

    post_video_data = get_post_data(access_token, sid, avid)

    status = True
    with ThreadPoolExecutor(max_workers=thread_pool_workers) as tpe:
        t_list = []
        for video_part in parts:
            print("upload {} added in pool".format(video_part.title))
            t_obj = tpe.submit(upload_video_part, access_token, sid, mid, video_part, max_retry)
            t_obj.video_part = video_part
            t_list.append(t_obj)

        for t_obj in as_completed(t_list):
            status = status and t_obj.result()
            print("video part {} finished, status: {}".format(t_obj.video_part.title, t_obj.result()))
            if not status:
                print("upload failed")
                return None, None

    headers = {
        'Connection': 'keep-alive',
        'Content-Type': 'application/json',
        'User-Agent': '',
    }
    submit_data = {
        'aid': avid,
        'build': 1054,
        'copyright': post_video_data["archive"]["copyright"],
        'cover': post_video_data["archive"]["cover"],
        'desc': post_video_data["archive"]["desc"],
        'no_reprint': post_video_data["archive"]["no_reprint"],
        'open_elec': post_video_data["archive_elec"]["state"], # open_elec not tested
        'source': post_video_data["archive"]["source"],
        'tag': post_video_data["archive"]["tag"],
        'tid': post_video_data["archive"]["tid"],
        'title': post_video_data["archive"]["title"],
        'videos': post_video_data["videos"]
    }

    # cover
    if os.path.isfile(cover):
        try:
            cover = upload_cover(access_token, sid, cover)
        except:
            cover = ''
    else:
        cover = ''

    # edit archive data
    if copyright:
        submit_data["copyright"] = copyright
    if title:
        submit_data["title"] = title
    if tid:
        submit_data["tid"] = tid
    if tag:
        submit_data["tag"] = tag
    if desc:
        submit_data["desc"] = desc
    if source:
        submit_data["source"] = source
    if cover:
        submit_data["cover"] = cover
    if no_reprint:
        submit_data["no_reprint"] = no_reprint
    if open_elec:
        submit_data["open_elec"] = open_elec

    if type(insert_index) is int:
        for i, video_part in enumerate(parts):
            submit_data['videos'].insert(insert_index + i, {
                "desc": video_part.desc,
                "filename": video_part.server_file_name,
                "title": video_part.title
            })
    elif insert_index is None:
        for video_part in parts:
            submit_data['videos'].append({
                "desc": video_part.desc,
                "filename": video_part.server_file_name,
                "title": video_part.title
            })
    else:
        print("wrong insert index")
        return None, None

    params = {
        'access_key': access_token,
    }
    params['sign'] = cipher.sign_dict(params, APPSECRET)
    r = requests.post(
        url="http://member.bilibili.com/x/vu/client/edit",
        params=params,
        headers=headers,
        verify=False,
        cookies={
            'sid': sid
        },
        json=submit_data,
    )

    print("edit submit")
    print(r.status_code)
    print(r.content.decode())

    data = r.json()["data"]
    return data["aid"], data["bvid"]
