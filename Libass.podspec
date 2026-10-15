native = File.directory?(File.join(__dir__, 'Frameworks')) ? '' : 'build/ios-native/pods/FFmpegKit/'

Pod::Spec.new do |s|
  s.name = 'Libass'
  s.version = '0.17.1'
  s.summary = 'Locally built libass and text shaping dependencies'
  s.homepage = 'https://github.com/libass/libass'
  s.authors = { 'libass contributors' => 'libass@libass.org' }
  s.license = { :type => 'ISC', :file => "#{native}Licenses/libass/COPYING" }
  s.source = { :git => 'https://github.com/libass/libass.git', :tag => '0.17.1' }
  s.ios.deployment_target = '16.0'
  s.static_framework = true
  s.vendored_frameworks = "#{native}Frameworks/libfreetype.xcframework", "#{native}Frameworks/libfribidi.xcframework",
                          "#{native}Frameworks/libharfbuzz.xcframework", "#{native}Frameworks/libass.xcframework"
  s.frameworks = 'CoreText', 'CoreGraphics', 'CoreFoundation'
  s.libraries = 'c++', 'z', 'iconv'
end
