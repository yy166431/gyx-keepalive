//
//  KeepAlive.m
//  静音音频后台保活模块（TrollStore / 侧载通用）
//
//  原理：iOS 不给普通第三方 App 真正的后台执行权。唯一稳定可用的手段是
//  声明 UIBackgroundModes=audio，然后进程内持续播放一段无声音频，让系统
//  认为这是一个"正在播放音乐"的 App，从而不挂起进程。这样主程序里 dlopen
//  加载的 刺客fuck.dylib（流量重定向 / SSL hook / 定时器）就能持续存活。
//
//  能改善但非 100%：锁屏久了、系统内存告急仍可能被回收。这是 iOS 的硬线。
//

#import <Foundation/Foundation.h>
#import <AVFoundation/AVFoundation.h>
#import <UIKit/UIKit.h>

@interface GYXKeepAlive : NSObject
@property (nonatomic, strong) AVAudioPlayer *player;
@property (nonatomic, assign) UIBackgroundTaskIdentifier bgTask;
+ (instancetype)shared;
- (void)start;
@end

@implementation GYXKeepAlive

+ (instancetype)shared {
    static GYXKeepAlive *inst = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        inst = [[GYXKeepAlive alloc] init];
        inst.bgTask = UIBackgroundTaskInvalid;
    });
    return inst;
}

// 定位 bundle 内的静音音频文件
- (NSURL *)silentAudioURL {
    NSBundle *mb = [NSBundle mainBundle];
    // 优先找我们放进去的 silence.caf / silence.wav / silence.mp3
    for (NSString *name in @[@"silence"]) {
        for (NSString *ext in @[@"caf", @"wav", @"mp3", @"m4a"]) {
            NSString *p = [mb pathForResource:name ofType:ext];
            if (p && [[NSFileManager defaultManager] fileExistsAtPath:p]) {
                return [NSURL fileURLWithPath:p];
            }
        }
    }
    return nil;
}

- (void)configureSession {
    AVAudioSession *session = [AVAudioSession sharedInstance];
    NSError *err = nil;
    // playback + mixWithOthers：可后台播放，且不打断别的 App 的声音
    [session setCategory:AVAudioSessionCategoryPlayback
             withOptions:AVAudioSessionCategoryOptionMixWithOthers
                   error:&err];
    if (err) NSLog(@"[GYXKeepAlive] setCategory err: %@", err);
    err = nil;
    [session setActive:YES error:&err];
    if (err) NSLog(@"[GYXKeepAlive] setActive err: %@", err);
}

- (void)start {
    [self configureSession];

    NSURL *url = [self silentAudioURL];
    if (!url) {
        NSLog(@"[GYXKeepAlive] 未找到 silence 音频文件，保活无法启动");
        return;
    }

    NSError *err = nil;
    self.player = [[AVAudioPlayer alloc] initWithContentsOfURL:url error:&err];
    if (err || !self.player) {
        NSLog(@"[GYXKeepAlive] player 初始化失败: %@", err);
        return;
    }
    self.player.numberOfLoops = -1;   // 无限循环
    self.player.volume = 0.0;         // 静音
    [self.player prepareToPlay];
    [self.player play];
    NSLog(@"[GYXKeepAlive] 静音保活已启动");

    // 监听中断（来电、闹钟等），结束后自动恢复
    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(handleInterruption:)
                                                 name:AVAudioSessionInterruptionNotification
                                               object:nil];
    // 监听音频路由变化（插拔耳机等），恢复播放
    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(handleRouteChange:)
                                                 name:AVAudioSessionRouteChangeNotification
                                               object:nil];
    // 进入后台时申请后台任务，进一步续命
    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(handleDidEnterBackground:)
                                                 name:UIApplicationDidEnterBackgroundNotification
                                               object:nil];
    [[NSNotificationCenter defaultCenter] addObserver:self
                                             selector:@selector(handleWillEnterForeground:)
                                                 name:UIApplicationWillEnterForegroundNotification
                                               object:nil];
}

- (void)ensurePlaying {
    [self configureSession];
    if (self.player && !self.player.isPlaying) {
        [self.player play];
        NSLog(@"[GYXKeepAlive] 恢复播放");
    }
}

- (void)handleInterruption:(NSNotification *)note {
    NSInteger type = [note.userInfo[AVAudioSessionInterruptionTypeKey] integerValue];
    if (type == AVAudioSessionInterruptionTypeEnded) {
        // 中断结束，延迟一点再恢复更稳
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            [self ensurePlaying];
        });
    }
}

- (void)handleRouteChange:(NSNotification *)note {
    [self ensurePlaying];
}

- (void)handleDidEnterBackground:(NSNotification *)note {
    UIApplication *app = [UIApplication sharedApplication];
    if (self.bgTask != UIBackgroundTaskInvalid) {
        [app endBackgroundTask:self.bgTask];
    }
    self.bgTask = [app beginBackgroundTaskWithName:@"GYXKeepAlive" expirationHandler:^{
        [app endBackgroundTask:self.bgTask];
        self.bgTask = UIBackgroundTaskInvalid;
    }];
    [self ensurePlaying];
}

- (void)handleWillEnterForeground:(NSNotification *)note {
    [self ensurePlaying];
}

@end

// dylib 被加载即执行。延迟到 App 起来后再启动音频，避免过早初始化失败。
__attribute__((constructor))
static void gyx_keepalive_init(void) {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        @try {
            [[GYXKeepAlive shared] start];
        } @catch (NSException *e) {
            NSLog(@"[GYXKeepAlive] start 异常: %@", e);
        }
    });
}
