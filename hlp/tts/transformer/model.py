import tensorflow as tf
from hlp.tts.utils.layers import ConvDropBN
from hlp.tts.utils.layers import DecoderPreNet
from hlp.utils.layers import positional_encoding
from hlp.utils.layers import transformer_encoder_layer
from hlp.utils.layers import transformer_decoder_layer


def encoder_pre_net(vocab_size: int, embedding_dim, conv_num,
                    filters, kernel_size, activation, dropout_rate):
    """
    :param vocab_size: 词汇大小
    :param embedding_dim: 嵌入层维度
    :param conv_num: 卷积层数量
    :param filters: 输出空间维数
    :param kernel_size: 卷积核大小
    :param activation: 激活方法
    :param dropout_rate: dropout采样率
    """
    inputs = tf.keras.Input(shape=(None,))
    outputs = tf.keras.layers.Embedding(vocab_size, embedding_dim)(inputs)

    for i in range(conv_num):
        outputs = ConvDropBN(filters=filters, kernel_size=kernel_size,
                             activation=activation, dropout_rate=dropout_rate)(outputs)
    outputs = tf.keras.layers.Dense(filters)(outputs)

    return tf.keras.Model(inputs=inputs, outputs=outputs)


def encoder(vocab_size: int, embedding_dim: int, conv_num, num_layers: int,
            filters: int, kernel_size: int, activation: str, units: int,
            num_heads: int, encoder_layer_dropout_rate: float = 0.1,
            pre_net_dropout_rate: float = 0.1, encoder_dropout_rate: float = 0.1):
    """
    transformer tts的encoder层
    :param vocab_size: 词汇大小
    :param embedding_dim: 嵌入层维度
    :param conv_num: 卷积层数量
    :param num_layers: encoder层数量
    :param filters: 输出空间维数
    :param kernel_size: 卷积核大小
    :param activation: 激活方法
    :param units: 单元大小
    :param pre_net_dropout_rate: pre_net的dropout采样率
    :param encoder_dropout_rate: encoder的dropout采样率
    :param encoder_layer_dropout_rate: encoder_layer的dropout采样率
    :param num_heads: 头注意力数量
    """
    inputs = tf.keras.Input(shape=(None,))
    padding_mask = tf.keras.Input(shape=(1, 1, None))
    pos_encoding = positional_encoding(vocab_size, filters)

    pre_net = encoder_pre_net(vocab_size, embedding_dim, conv_num,
                              filters, kernel_size, activation, pre_net_dropout_rate)(inputs)
    pre_net *= tf.math.sqrt(tf.cast(filters, tf.float32))
    pre_net += pos_encoding[:, :tf.shape(pre_net)[1], :]

    outputs = tf.keras.layers.Dropout(rate=encoder_dropout_rate)(pre_net)

    for i in range(num_layers):
        outputs = transformer_encoder_layer(units=units,
                                            d_model=filters, num_heads=num_heads,
                                            dropout=encoder_layer_dropout_rate)([outputs, padding_mask])

    return tf.keras.Model(inputs=[inputs, padding_mask], outputs=[outputs])


def decoder(max_mel_length: int, num_mel: int, pre_net_units: int, pre_net_layers_num: int,
            d_model: int, pre_net_dropout_rate: float = 0.1):
    """
    :param max_mel_length: 最长序列长度
    :param num_mel: 产生的梅尔带数
    :param pre_net_units: pre_net全连接层单元数
    :param pre_net_layers_num: pre_net层数
    :param d_model: 位置数量
    :param pre_net_dropout_rate: pre_net的dropout采样率
    """
    inputs = tf.keras.Input(shape=(max_mel_length, num_mel))
    enc_outputs = tf.keras.Input(shape=(None, d_model))
    look_ahead_mask = tf.keras.Input(shape=(1, None, None))
    padding_mask = tf.keras.Input(shape=(1, 1, None))

    decoder_pre_net = DecoderPreNet(pre_net_units, pre_net_layers_num,
                                    pre_net_dropout_rate)(inputs)