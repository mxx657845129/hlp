import os
import json
import jieba
import pysolr
import numpy as np
import tensorflow as tf
from sklearn.feature_extraction.text import TfidfVectorizer


def _add_start_end_token(start_sign: str, end_sign: str, sentence: str):
    """
    用于给句子首尾添加start和end
    :param start_sign: 开始标记
    :param end_sign: 结束标记
    :param sentence: 待处理句子
    :return: 合成之后的句子
    """
    sentence = start_sign + ' ' + sentence + ' ' + end_sign
    return sentence


def preprocess_request(sentence: str, token: dict, max_length: int, start_sign: str, end_sign: str):
    """
    用于处理回复功能的输入句子，返回模型使用的序列
    :param sentence: 待处理句子
    :param token: 字典
    :param max_length: 单个句子最大长度
    :param start_sign: 开始标记
    :param end_sign: 结束标记
    :return: 处理好的句子和decoder输入
    """
    sentence = " ".join(jieba.cut(sentence))
    sentence = _add_start_end_token(sentence, start_sign, end_sign)
    inputs = [token.get(i, 3) for i in sentence.split(' ')]
    inputs = tf.keras.preprocessing.sequence.pad_sequences([inputs], maxlen=max_length, padding='post')
    inputs = tf.convert_to_tensor(inputs)
    dec_input = tf.expand_dims([token[start_sign]], 0)

    return inputs, dec_input


def _create_dataset(data_path: str, num_examples: int, start_sign: str, end_sign: str):
    """
    用于将分词文本读入内存，并整理成问答对
    :param data_path: 分词文本路径
    :param num_examples: 读取的数据量大小
    :param start_sign: 开始标记
    :param end_sign: 结束标记
    :return: 整理好的问答对和样本权重
    """
    if not os.path.exists(data_path):
        print('不存在已经分词好的文件，请先执行pre_treat模式')
        exit(0)

    with open(data_path, 'r', encoding="utf-8") as file:
        lines = file.read().strip().split('\n')
        sample_weights = []
        qa_pairs = []
        if num_examples != 0:
            lines = lines[:num_examples]

        for line in lines:
            # 文本数据中的问答对权重通过在问答对尾部添加“<|>”配置
            temp = line.split("<|>")
            qa_pairs.append([_add_start_end_token(start_sign, end_sign, w) for w in temp[0].split('\t')])
            # 如果没有配置对应问答对权重，则默认为1.
            if len(temp) == 1:
                sample_weights.append(float(1))
            else:
                sample_weights.append(float(temp[1]))

    return zip(*qa_pairs), sample_weights


def _read_data(data_path: str, num_examples: int, start_sign: str, end_sign: str, max_length: int,
               tokenizer: tf.keras.preprocessing.text.Tokenizer = None):
    """
    读取数据，将input和target进行分词后返回
    :param data_path: 分词文本路径
    :param num_examples: 读取的数据量大小
    :param start_sign: 开始标记
    :param end_sign: 结束标记
    :param max_length: 最大序列长度
    :param tokenizer: 传入现有的分词器，默认重新生成
    :return: 输入序列张量、目标序列张量和分词器
    """
    (input_lang, target_lang), diag_weight = _create_dataset(data_path, num_examples, start_sign, end_sign)
    input_tensor, target_tensor, txt_tokenizer = _tokenize(input_lang, target_lang, max_length, tokenizer)
    return input_tensor, target_tensor, txt_tokenizer, diag_weight


def _tokenize(input_lang: list, target_lang: list, max_length: int,
              tokenizer: tf.keras.preprocessing.text.Tokenizer = None):
    """
    分词方法，使用Keras API中的Tokenizer进行分词操作
    :param input_lang: 输入序列
    :param target_lang: 目标序列
    :param max_length: 最大序列长度
    :param tokenizer: 传入现有的分词器，默认重新生成
    :return: 输入序列张量、目标序列张量和分词器
    """
    lang = np.hstack((input_lang, target_lang))
    if tokenizer is not None:
        txt_tokenizer = tokenizer
    else:
        txt_tokenizer = tf.keras.preprocessing.text.Tokenizer(filters='', oov_token=3)

    txt_tokenizer.fit_on_texts(lang)
    input_tensor = txt_tokenizer.texts_to_sequences(input_lang)
    target_tensor = txt_tokenizer.texts_to_sequences(target_lang)

    input_tensor = tf.keras.preprocessing.sequence.pad_sequences(input_tensor, maxlen=max_length,
                                                                 padding='post')
    target_tensor = tf.keras.preprocessing.sequence.pad_sequences(target_tensor, maxlen=max_length,
                                                                  padding='post')

    return input_tensor, target_tensor, txt_tokenizer


def load_data(dict_fn: str, data_fn: str, start_sign: str, end_sign: str, buffer_size: int,
              batch_size: int, checkpoint_dir: str, max_length: int, valid_data_split: float = 0.0,
              valid_data_fn: str = "", max_train_data_size: int = 0, max_valid_data_size: int = 0):
    """
    数据加载方法，含四个元素的元组，包括如下：
    :param dict_fn: 字典路径
    :param data_fn: 文本数据路径
    :param start_sign: 开始标记
    :param end_sign: 结束标记
    :param buffer_size: Dataset加载缓存大小
    :param batch_size: Dataset加载批大小
    :param checkpoint_dir: 检查点保存路径
    :param max_length: 单个句子最大长度
    :param valid_data_split: 用于从训练数据中划分验证数据
    :param valid_data_fn: 验证数据文本路径
    :param max_train_data_size: 最大训练数据量
    :param max_valid_data_size: 最大验证数据量
    :return: 训练Dataset、验证Dataset、训练数据总共的步数、验证数据总共的步数和检查点前缀
    """
    print("读取训练对话对...")
    train_input, train_target, txt_tokenizer, sample_weights = _read_data(data_fn, max_train_data_size,
                                                                        start_sign, end_sign, max_length)
    valid_flag = True  # 是否开启验证标记
    valid_steps_per_epoch = 0

    if valid_data_fn != "":
        print("读取验证对话对...")
        valid_input, valid_target, _, _ = _read_data(valid_data_fn, max_valid_data_size, start_sign,
                                                     end_sign, max_length, tokenizer=txt_tokenizer)
    elif valid_data_split != 0.0:
        train_size = int(len(train_input) * (1.0 - valid_data_split))
        valid_input = train_input[train_size:]
        valid_target = train_target[train_size:]
        train_input = train_input[:train_size]
        train_target = train_target[:train_size]
        sample_weights = sample_weights[:train_size]
    else:
        valid_flag = False

    print("保存词典到", dict_fn)
    with open(dict_fn, 'w', encoding='utf-8') as file:
        file.write(json.dumps(txt_tokenizer.word_index, indent=4, ensure_ascii=False))

    train_dataset = tf.data.Dataset.from_tensor_slices((train_input, train_target, sample_weights)).cache().shuffle(
        buffer_size).prefetch(tf.data.experimental.AUTOTUNE)
    train_dataset = train_dataset.batch(batch_size, drop_remainder=True)

    if valid_flag:
        valid_dataset = tf.data.Dataset.from_tensor_slices((valid_input, valid_target)).cache().shuffle(
            buffer_size).prefetch(tf.data.experimental.AUTOTUNE)
        valid_dataset = valid_dataset.batch(batch_size, drop_remainder=True)
        valid_steps_per_epoch = len(valid_input) // batch_size
    else:
        valid_dataset = None

    checkpoint_prefix = os.path.join(checkpoint_dir, "ckpt")
    steps_per_epoch = len(train_input) // batch_size

    return train_dataset, valid_dataset, steps_per_epoch, valid_steps_per_epoch, checkpoint_prefix


def load_token_dict(dict_fn: str):
    """
    加载字典方
    :param dict_fn: 字典路径
    :return: token: 字典
    """
    if not os.path.exists(dict_fn):
        print("不存在字典文件，请先执行train模式并生成字典文件")
        exit(0)

    with open(dict_fn, 'r', encoding='utf-8') as file:
        token = json.load(file)

    return token


def sequences_to_texts(sequences: list, token_dict: dict):
    """
    将序列转换成text
    :param sequences: 待处理序列
    :param token_dict: 字典文本路径
    :return: 处理完成的序列
    """
    inv = {}
    for key, value in token_dict.items():
        inv[value] = key

    result = []
    for text in sequences:
        temp = ''
        for token in text:
            temp = temp + ' ' + inv[token]
        result.append(temp)
    return result


def dict_texts_to_sequences(texts: list, token_dict: dict):
    """
    将text转换成序列
    :param texts: 文本列表
    :param token_dict: 字典
    :return: 序列列表
    """
    result = []
    for text in texts:
        result.append([token_dict.get(element, 1) for element in text.split(" ")])

    return result


def smn_load_train_data(dict_fn: str, data_fn: str, checkpoint_dir: str, buffer_size: int,
                        batch_size: int, max_utterance: int, max_sentence: int, max_train_data_size: int = 0):
    """
    用于SMN的训练数据加载
    :param dict_fn: 字典文本路径
    :param data_fn: 数据文本路径
    :param buffer_size: Dataset加载缓存大小
    :param batch_size: Dataset加载批大小
    :param checkpoint_dir: 检查点保存路径
    :param max_utterance: 每轮对话最大对话数
    :param max_sentence: 单个句子最大长度
    :param max_train_data_size: 最大训练数据量
    :return: TensorFlow的数据处理类、分词器、检查点前缀和总的步数
    """
    is_exist = os.path.exists(data_fn)
    if not is_exist:
        print('不存在训练数据集，请添加数据集之后重试')
        exit(0)

    print('正在读取文本数据...')
    history = []  # 用于保存每轮对话历史语句
    response = []  # 用于保存每轮对话的回答
    label = []  # 用于保存每轮对话的标签
    store = []  # 作为中间介质保存所有语句，用于字典处理
    count = 0  # 用于处理数据计数

    with open(data_fn, 'r', encoding='utf-8') as file:
        if max_train_data_size == 0:
            lines = file.read().strip().split('\n')
        else:
            lines = file.read().strip().split('\n')[:max_train_data_size]

        for line in lines:
            count += 1
            apart = line.split('\t')
            store.extend(apart[0:])
            label.append(int(apart[0]))
            response.append(apart[-1])
            del apart[0]
            del apart[-1]
            history.append(apart)
            if count % 100 == 0:
                print('已读取 {} 轮对话'.format(count))

    print('数据读取完成，正在生成字典并保存...')
    tokenizer = tf.keras.preprocessing.text.Tokenizer(filters='', oov_token='<UNK>')
    tokenizer.fit_on_texts(store)

    with open(dict_fn, 'w', encoding='utf-8') as file:
        file.write(json.dumps(tokenizer.word_index, indent=4, ensure_ascii=False))
    print('字典已保存，正在整理数据，生成训练数据...')
    response = tokenizer.texts_to_sequences(response)
    response = tf.keras.preprocessing.sequence.pad_sequences(response, maxlen=max_sentence, padding="post")

    count = 0
    utterances = []
    for utterance in history:
        count += 1
        pad_sequences = [0] * max_sentence
        # 注意了，这边要取每轮对话的最后max_utterances数量的语句
        utterance_padding = tokenizer.texts_to_sequences(utterance)[-max_utterance:]
        utterance_len = len(utterance_padding)
        # 如果当前轮次中的历史语句不足max_utterances数量，需要在尾部进行填充
        if utterance_len != 10:
            utterance_padding += [pad_sequences] * (max_utterance - utterance_len)
        utterances.append(tf.keras.preprocessing.sequence.pad_sequences(utterance_padding, maxlen=max_sentence,
                                                                        padding="post").tolist())

        if count % 100 == 0:
            print('已生成 {} 轮训练数据'.format(count))

    print('数据生成完毕，正在转换为Dataset...')
    dataset = tf.data.Dataset.from_tensor_slices((utterances, response, label)).cache().shuffle(
        buffer_size).prefetch(tf.data.experimental.AUTOTUNE)
    dataset = dataset.batch(batch_size, drop_remainder=True)
    checkpoint_prefix = os.path.join(checkpoint_dir, 'ckpt')
    steps_per_epoch = len(utterances) // batch_size
    print('训练数据处理完成，正在进行训练...')

    return dataset, tokenizer, checkpoint_prefix, steps_per_epoch


def load_smn_valid_data(data_fn: str, max_sentence: int, max_utterance: int, max_valid_data_size: int,
                        token_dict: dict = None, tokenizer: tf.keras.preprocessing.text.Tokenizer = None,
                        max_turn_utterances_num: int = 10):
    """
    用于单独加载smn的评价数据，这个方法设计用于能够同时在train时进行评价，以及单独evaluate模式中使用
    注意了，这里token_dict和必传其一，同时传只使用tokenizer
    :param data_fn: 评价数据地址
    :param max_sentence: 最大句子长度
    :param max_utterance: 最大轮次语句数量
    :param max_valid_data_size: 最大验证数据量
    :param token_dict: 字典地址
    :param tokenizer: 分词器实例
    :param max_turn_utterances_num: dataset的批量，最好取单轮对话正负样本数总和的倍数
    :return: dataset
    """
    if not os.path.exists(data_fn):
        return

    history = []
    response = []
    label = []
    with open(data_fn, 'r', encoding='utf-8') as file:
        lines = file.read().strip().split("\n")[:max_valid_data_size]
        for line in lines:
            apart = line.split("\t")
            label.append(int(apart[0]))
            response.append(apart[-1])
            del apart[0]
            del apart[-1]
            history.append(apart)

    if tokenizer is not None:
        response = tokenizer.texts_to_sequences(response)
    else:
        response = dict_texts_to_sequences(response, token_dict)
    response = tf.keras.preprocessing.sequence.pad_sequences(response, maxlen=max_sentence, padding="post")

    utterances = []
    for utterance in history:
        pad_sequences = [0] * max_sentence
        if tokenizer is not None:
            utterance_padding = tokenizer.texts_to_sequences(utterance)[-max_utterance:]
        else:
            utterance_padding = dict_texts_to_sequences(utterance, token_dict)[-max_utterance:]

        utterance_len = len(utterance_padding)
        # 如果当前轮次中的历史语句不足max_utterances数量，需要在尾部进行填充
        if utterance_len != max_utterance:
            utterance_padding += [pad_sequences] * (max_utterance - utterance_len)
        utterances.append(tf.keras.preprocessing.sequence.pad_sequences(utterance_padding, maxlen=max_sentence,
                                                                        padding="post").tolist())

    # 在这里不对数据集进行打乱，方便用于指标计算
    dataset = tf.data.Dataset.from_tensor_slices((utterances, response, label)).prefetch(
        tf.data.experimental.AUTOTUNE)
    dataset = dataset.batch(max_turn_utterances_num, drop_remainder=True)

    return dataset


def get_tf_idf_top_k(history: list, k: int = 5):
    """
    使用tf_idf算法计算权重最高的k个词，并返回
    :param history: 上下文语句
    :param k: 返回词数量
    :return: top_5_key
    """
    tf_idf = {}

    vectorizer = TfidfVectorizer(analyzer='word')
    weights = vectorizer.fit_transform(history).toarray()[-1]
    key_words = vectorizer.get_feature_names()

    for i in range(len(weights)):
        tf_idf[key_words[i]] = weights[i]

    top_k_key = []
    tf_idf_sorted = sorted(tf_idf.items(), key=lambda x: x[1], reverse=True)[:k]
    for element in tf_idf_sorted:
        top_k_key.append(element[0])

    return top_k_key


def creat_index_dataset(data_fn: str, solr_sever: str, max_database_size: int):
    """
    生成轮次tf-idf为索引的候选回复
    :param data_fn: 文本数据路径
    :param solr_sever: solr服务的地址
    :param max_database_size: 从文本中读取最大数据量
    :return: 无返回值
    """
    if not os.path.exists(data_fn):
        print("没有找到对应的文本数据，请确认文本数据存在")
        exit(0)

    responses = []
    count = 0
    solr = pysolr.Solr(url=solr_sever, always_commit=True)
    solr.ping()

    print("检测到对应文本，正在处理文本数据...")
    with open(data_fn, 'r', encoding='utf-8') as file:
        if max_database_size == 0:
            lines = file.read().strip().split("\n")
        else:
            lines = file.read().strip().split("\n")[:max_database_size]
        lines = lines[::2]

        for line in lines:
            count += 1
            apart = line.split("\t")[1:]
            for i in range(len(apart)):
                responses.append({
                    "utterance": apart[i]
                })

            if count % 100 == 0:
                print("已处理了 {} 轮次对话".format(count))
    solr.delete(q="*:*")
    solr.add(docs=responses)

    print("文本处理完毕，已更新候选回复集")
