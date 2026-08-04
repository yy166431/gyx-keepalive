//
//  KeepAlive.m  —  纯外部注入版后台保活（TrollFools 注入用）
//
//  设计目标：只注入这一个 dylib 到原版 App，不改 Info.plist、不依赖任何外部
//  资源文件，即可让 App 进后台后保持存活，从而让 App 内 dlopen 的业务 dylib
//  （流量重定向 / 定时器等）持续运行。
//
//  手段（组合，互为兜底）：
//   1) 运行时激活 AVAudioSession(playback + mixWithOthers) 并循环播放
//      一段【代码内构造】的静音 PCM —— 让系统把本进程当作正在放音乐而不挂起。
//      巨魔安装的 App 经 TrollFools 重签后带高权限，运行时激活通常无需
//      Info.plist 里的 UIBackgroundModes=audio 也能后台出声。
//   2) 进后台时 beginBackgroundTask 续期，并起定时器周期性确保音频在播。
//   3) 监听中断 / 路由变化 / 前后台切换，自动恢复播放。
//
//  说明：仍非 100%（iOS 硬限制），但这是注入式插件的通用做法。
//

#import <Foundation/Foundation.h>
#import <AVFoundation/AVFoundation.h>
#import <UIKit/UIKit.h>

// 运行时构造一段静音 WAV（无外部文件依赖）。arm64 小端，可直接拼字节。
static NSData *GYXMakeSilentWav(double seconds, int rate) {
    int channels = 1, bits = 16;
    int32_t dataBytes  = (int32_t)(seconds * rate) * channels * (bits / 8);
    int32_t byteRate   = rate * channels * (bits / 8);
    int16_t blockAlign = (int16_t)(channels * (bits / 8));
    int32_t chunkSize  = 36 + dataBytes;
    int32_t sub1 = 16, sr = rate, ds = dataBytes;
    int16_t fmt = 1, ch = (int16_t)channels, bps = (int16_t)bits;

    NSMutableData *d = [NSMutableData data];
    [d appendBytes:"RIFF" length:4];
    [d appendBytes:&chunkSize length:4];
    [d appendBytes:"WAVE" length:4];
    [d appendBytes:"fmt " length:4];
    [d appendBytes:&sub1 length:4];
    [d appendBytes:&fmt length:2];
    [d appendBytes:&ch length:2];
    [d appendBytes:&sr length:4];
    [d appendBytes:&byteRate length:4];
    [d appendBytes:&blockAlign length:2];
    [d appendBytes:&bps length:2];
    [d appendBytes:"data" length:4];
    [d appendBytes:&ds length:4];
    [d appendData:[NSMutableData dataWithLength:dataBytes]];  // 全零 = 静音
    return d;
}

@interface GYXKeepAlive : NSObject <AVAudioPlayerDelegate>
@property (nonatomic, strong) AVAudioPlayer *player;
@property (nonatomic, strong) NSTimer *guardTimer;
@property (nonatomic, assign) UIBackgroundTaskIdentifier bgTask;
+ (instancetype)shared;
- (void)start;
@end

@implementation GYXKeepAlive

+ (instancetype)shared {
    static GYXKeepAlive *inst = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        inst = [[GYXKeepAlive alloc] init];
        inst.bgTask = UIBackgroundTaskInvalid;
    });
    return inst;
}

- (void)configureSession {
    AVAudioSession *s = [AVAudioSession sharedInstance];
    NSError *e = nil;
    [s setCategory:AVAudioSessionCategoryPlayback
       withOptions:AVAudioSessionCategoryOptionMixWithOthers error:&e];
    if (e) NSLog(@"[GYXKeepAlive] setCategory: %@", e);
    e = nil;
    [s setActive:YES error:&e];
    if (e) NSLog(@"[GYXKeepAlive] setActive: %@", e);
}

- (void)start {
    [self configureSession];

    NSData *wav = GYXMakeSilentWav(2.0, 44100);
    NSError *e = nil;
    self.player = [[AVAudioPlayer alloc] initWithData:wav error:&e];
    if (e || !self.player) { NSLog(@"[GYXKeepAlive] player: %@", e); return; }
    self.player.delegate = self;
    self.player.numberOfLoops = -1;   // 无限循环
    self.player.volume = 0.0;         // 静音
    [self.player prepareToPlay];
    [self.player play];
    NSLog(@"[GYXKeepAlive] 保活已启动 playing=%d", self.player.isPlaying);

    NSNotificationCenter *nc = [NSNotificationCenter defaultCenter];
    [nc addObserver:self selector:@selector(onInterruption:)
               name:AVAudioSessionInterruptionNotification object:nil];
    [nc addObserver:self selector:@selector(onRouteChange:)
               name:AVAudioSessionRouteChangeNotification object:nil];
    [nc addObserver:self selector:@selector(onEnterBackground:)
               name:UIApplicationDidEnterBackgroundNotification object:nil];
    [nc addObserver:self selector:@selector(onEnterForeground:)
               name:UIApplicationWillEnterForegroundNotification object:nil];

    // 守护定时器：每 10s 确保还在播（后台被降频也仍会周期触发）
    self.guardTimer = [NSTimer scheduledTimerWithTimeInterval:10.0
                                                       target:self
                                                     selector:@selector(ensurePlaying)
                                                     userInfo:nil repeats:YES];
}

- (void)ensurePlaying {
    if (!self.player) return;
    if (!self.player.isPlaying) {
        [self configureSession];
        [self.player play];
        NSLog(@"[GYXKeepAlive] 恢复播放");
    }
}

- (void)audioPlayerDidFinishPlaying:(AVAudioPlayer *)p successfully:(BOOL)flag {
    // numberOfLoops=-1 理论上不会触发，兜底重播
    [self.player play];
}

- (void)onInterruption:(NSNotification *)n {
    NSInteger t = [n.userInfo[AVAudioSessionInterruptionTypeKey] integerValue];
    if (t == AVAudioSessionInterruptionTypeEnded) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.4 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{ [self ensurePlaying]; });
    }
}

- (void)onRouteChange:(NSNotification *)n { [self ensurePlaying]; }

- (void)onEnterBackground:(NSNotification *)n {
    UIApplication *app = [UIApplication sharedApplication];
    if (self.bgTask != UIBackgroundTaskInvalid) [app endBackgroundTask:self.bgTask];
    self.bgTask = [app beginBackgroundTaskWithName:@"GYXKeepAlive" expirationHandler:^{
        [app endBackgroundTask:self.bgTask];
        self.bgTask = UIBackgroundTaskInvalid;
    }];
    [self ensurePlaying];
}

- (void)onEnterForeground:(NSNotification *)n { [self ensurePlaying]; }

@end

__attribute__((constructor))
static void gyx_keepalive_init(void) {
    // 延迟到 App 起来后再启动，避免过早初始化失败
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        @try { [[GYXKeepAlive shared] start]; }
        @catch (NSException *ex) { NSLog(@"[GYXKeepAlive] start 异常: %@", ex); }
    });
}
