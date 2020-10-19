import tensorflow as tf
from transformers import GPT2Config
import os
import numpy as np
from tqdm import tqdm  # 可以在控制台显示进度条
from sklearn.model_selection import train_test_split
from transformers import TFGPT2LMHeadModel
from transformers import BertTokenizer
# 不能是一一对应 得是理解了之后的重新编写
import train_args as train_args
import preprocess_data as preprocess_data

# 数据处理
PAD = '[PAD]'
pad_id = 0
BATCH_SIZE = 3


def create_model(args, vocab_size):
    """
    :param args:
    :param vocab_size:字典大小
    :return:
    """
    print('配置模型参数')
    # model_config = GPT2Config.from_json_file('config/model_config_dialogue_small.json')
    print('创建model')
    # model = TFGPT2LMHeadModel.from_pretrained('gpt2')
    if args.pretrained_model:  # 如果指定了预训练的GPT2模型
        model = TFGPT2LMHeadModel.from_pretrained(args.pretrained_model)
    else:  # 若没有指定预训练模型，则初始化模型
        print('初始化模型')
        model_config = GPT2Config.from_json_file(args.model_config)
        print('config:\n' + model_config.to_json_string())
        model = TFGPT2LMHeadModel(config=model_config)
        print('构造好模型')
        # 根据tokenizer的vocabulary调整GPT2模型的voca的大小
    # model.resize_token_embeddings(vocab_size)

    # model = TFGPT2LMHeadModel.from_pretrained()#实例化一个类
    return model, model.config.to_dict().get("n_ctx")


def change_tpye(outputs, labels):
    logits = outputs[0]  # (batch,len,vocab)

    shift_labels = labels[:, 1:]  # (batch,len)
    shift_logits = logits[:, :-1, ]  # (batch,len,vocab)

    shift_labels = tf.convert_to_tensor(shift_labels)
    shift_logits = tf.reshape(shift_logits, [-1, tf.convert_to_tensor(shift_logits).shape[-1]])  # (batch*len,vocab)
    shift_labels = tf.reshape(shift_labels, [-1])  # (batch*len,)

    return shift_logits, shift_labels


def loss_function(shift_logits, shift_labels, tokenizer):
    mask = tf.math.logical_not(tf.math.equal(shift_labels, 0))
    loss_object = tf.keras.losses.SparseCategoricalCrossentropy(
        from_logits=True, reduction='none')
    loss_ = loss_object(shift_labels, shift_logits)
    print('loss_={}'.format(loss_))
    mask = tf.cast(mask, dtype=loss_.dtype)
    mask = tf.reshape(mask, [-1])
    loss_ *= mask
    loss = tf.reduce_mean(loss_)

    preds = np.argmax(shift_logits, axis=1)  # preds表示对应的prediction_score预测出的token在voca中的id。维度为[batch_size,token_len]
    print('预测id={}'.format(preds))

    correct = 0  # 计算model预测正确的token的个数，排除pad的tokne
    text = tokenizer.convert_ids_to_tokens(preds)
    print('预测的文本序列：={}'.format(text))
    for i in range(len(preds)):
        # text = tokenizer.convert_ids_to_tokens(preds[i])
        # print('text={}'.format(text))
        # print('shift_labels={}'.format(shift_labels[i]))
        if (preds[i] == shift_labels[i]):
            correct += 1
    print('correct={}'.format(correct))
    accuracy = correct / len(preds)
    return loss, accuracy


def load_checkpoint(model, optimizer, args):
    # 加载检查点
    checkpoint_path = args.dialogue_model_output_path
    ckpt = tf.train.Checkpoint(model=model, optimizer=optimizer)
    ckpt_manager = tf.train.CheckpointManager(ckpt, checkpoint_path, max_to_keep=5)
    if ckpt_manager.latest_checkpoint:
        ckpt.restore(ckpt_manager.latest_checkpoint)
        print('已恢复至最新的检查点！')


def train_step(model, input_ids, optimizer, tokenizer):
    with tf.GradientTape() as t:
        outputs = model(inputs=input_ids)
        shift_logits, shift_labels = change_tpye(outputs, input_ids)
        loss, accuracy = loss_function(shift_logits, shift_labels, tokenizer)
        print('loss={}'.format(loss))
    gradients = t.gradient(loss, model.trainable_variables)
    optimizer.apply_gradients(zip(gradients, model.trainable_variables))
    return loss, accuracy


def train(model, train_list, args, tokenizer, optimizer):
    train_dataset, max_input_len = preprocess_data.collate_fn(train_list)
    new_list = []
    for i in range(len(train_dataset)):
        s = list(map(int, train_dataset[i]))
        new_list.append(s)
    dd = tf.convert_to_tensor(new_list)
    train_dataset = tf.data.Dataset.from_tensor_slices(dd)
    dataset = train_dataset.batch(BATCH_SIZE, drop_remainder=True)  # drop_remainder 忽略最后一个不足数量的batch
    # 数据读取
    print('dataset={}'.format(dataset))

    checkpoint_path = args.dialogue_model_output_path

    ckpt = tf.train.Checkpoint(model=model, optimizer=optimizer)

    ckpt_manager = tf.train.CheckpointManager(ckpt, checkpoint_path, max_to_keep=5)
    if ckpt_manager.latest_checkpoint:
        ckpt.restore(ckpt_manager.latest_checkpoint)
        print('已恢复至最新检查点！')
    print("开始训练...")

    optimizer = tf.keras.optimizers.Adam(learning_rate=0.1)
    # # 开始训练
    for epoch in range(args.epochs):
        for batch_idx, input_ids in enumerate(dataset):
            loss, accuracy = train_step(model, input_ids, optimizer, tokenizer)
        batch_loss = (loss / max_input_len)
        print('epoch={} loss={} accuracy={} '.format(epoch, batch_loss, accuracy))
    # if (epoch + 1) % 5 == 0:
    ckpt_save_path = ckpt_manager.save()
    print('已保存 训练 ckpt_save_path={}'.format(ckpt_save_path))
    # print('Saving checkpoint for epoch {} at {}'.format(epoch + 1,
    #                                                       ckpt_save_path))


def main():
    args = train_args.setup_train_args()
    if args.seed:
        train_args.set_random_seed(args)
    # 初始化tokenizer
    tokenizer = BertTokenizer(vocab_file=args.vocab_path)
    # tokenizer的字典大小
    vocab_size = len(tokenizer)
    # print('vocab_size{}'.format(vocab_size))
    global pad_id
    # pad_id = tokenizer.convert_tokens_to_ids(PAD)

    # 创建对话模型的输出目录
    if not os.path.exists(args.dialogue_model_output_path):
        os.mkdir(args.dialogue_model_output_path)

    # 加载GPT2模型
    model, n_ctx = create_model(args, vocab_size)
    # print('n_ctx={}'.format(n_ctx))
    # 对原始数据进行预处理,将原始语料转换成对应的token_id
    # 如果当前是要训练对话生成模型
    print('开始产生token')
    # 不修改数据集的情况下，没必要每次训练都运行preprocess_raw_data 因为 生成的data是一样的
    # preprocess_data.preprocess_raw_data(args, tokenizer, n_ctx)
    # 进行数据类型变换
    with open(args.train_tokenized_path, "r", encoding="utf8") as f:
        data_list = []
        # 一行行地读取 str类型的data  然后转换为list形式
        for line in f.readlines():
            data = line.strip()
            data = data.split(' ')
            data_list.append(data)
    optimizer = tf.keras.optimizers.Adam(learning_rate=0.1)

    train_list, test_list = train_test_split(data_list, test_size=0.1, random_state=1)

    print('开始训练')
    train(model, train_list, args, tokenizer, optimizer)
    print('训练结束')


if __name__ == '__main__':
    main()
